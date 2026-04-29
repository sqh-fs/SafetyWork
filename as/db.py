"""AS 认证服务器的数据访问层。

两表化后，本模块只访问图片设计中的两张表:
- user_account: 保存用户账号、PBKDF2 密码摘要、登录代数 login_gen 和状态。
- security_event_log: 记录注册、登录、改密等安全事件。

本模块不负责协议解析、密码学计算或 WebSocket 响应，只封装 SQL。调用者
需要显式 commit 或 rollback，这样注册、登录、改密主流程可以把用户变更和
安全事件写入放在同一个事务边界内。
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterator, Optional

from config import DbConfig

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError as exc:  # pragma: no cover - only hit when dependencies missing.
    pymysql = None
    DictCursor = None
    _PYMYSQL_IMPORT_ERROR = exc
else:
    _PYMYSQL_IMPORT_ERROR = None


class DatabaseError(RuntimeError):
    """数据库访问错误。

    典型场景:
    - 未安装 pymysql。
    - MySQL 无法连接。
    - schema_auth.sql 尚未初始化。
    """

    pass


@dataclass(frozen=True)
class SecurityEvent:
    """security_event_log 的一条待写入事件。

    参数:
    - user_id: 已知用户的 user_account.user_id；用户不存在时允许为 None。
    - username: 用户名快照，用于追踪登录失败或非法请求。
    - event_type: REGISTER、LOGIN_SUCCESS、LOGIN_FAIL、CHANGE_PASSWORD 等。
    - result: 1 表示成功，0 表示失败。
    - client_id: 客户端运行期实例 ID，来自报文 clientId。
    - remote_addr: WebSocket 远端地址，仅用于审计。
    - reason: 失败原因或安全事件原因，例如 BAD_CREDENTIALS。
    """

    user_id: Optional[int]
    username: Optional[str]
    event_type: str
    result: int
    client_id: Optional[str]
    remote_addr: Optional[str]
    reason: Optional[str]


class AuthDao:
    """认证库 DAO。

    输入:
    - DbConfig: MySQL 连接参数。

    输出:
    - connection(): 返回可用于事务操作的 PyMySQL 连接。
    - 各方法返回 dict、int 或 None。

    事务约定:
    - 本类默认不自动 commit。
    - 写方法执行 SQL 后由调用者决定 commit/rollback。
    """

    def __init__(self, config: DbConfig) -> None:
        self.config = config

    def _ensure_driver(self) -> None:
        """确认 pymysql 已安装。

        异常:
        - DatabaseError: as/requirements.txt 中的 pymysql 未安装。
        """

        if pymysql is None:
            raise DatabaseError(
                "pymysql is required; install dependencies from as/requirements.txt"
            ) from _PYMYSQL_IMPORT_ERROR

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """创建一个 MySQL 连接。

        返回:
        - PyMySQL connection，游标类型为 DictCursor，查询结果是字典。

        副作用:
        - 打开 TCP/MySQL 连接。
        - 退出上下文时关闭连接，但不会替调用者自动提交事务。
        """

        self._ensure_driver()
        conn = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset=self.config.charset,
            autocommit=False,
            cursorclass=DictCursor,
        )
        try:
            yield conn
        finally:
            conn.close()

    def ping(self) -> None:
        """检查数据库是否可连接。

        输入:
        - DbConfig 中的 MySQL 参数。

        输出:
        - None。连接成功后立即关闭。

        异常:
        - DatabaseError 或 PyMySQL 原始异常。
        """

        with self.connection() as conn:
            conn.ping(reconnect=False)

    def find_user(
        self,
        conn: Any,
        username: str,
        *,
        for_update: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """按规范化用户名查询 user_account。

        参数:
        - conn: 外部传入的事务连接。
        - username: 已经 trim + lower 的用户名。
        - for_update: True 时追加 FOR UPDATE，用于登录/改密时锁定 login_gen。

        返回:
        - 找到时返回 user_account 行字典。
        - 不存在时返回 None。

        数据库副作用:
        - 只读查询；for_update=True 时会在当前事务内加行锁。
        """

        sql = """
            SELECT user_id, username, password_hash, password_salt, pbkdf2_iter,
                   login_gen, status, last_login_at, created_at, updated_at
            FROM user_account
            WHERE username = %s
        """
        if for_update:
            sql += " FOR UPDATE"
        with conn.cursor() as cur:
            cur.execute(sql, (username,))
            return cur.fetchone()

    def create_user(
        self,
        conn: Any,
        *,
        username: str,
        password_hash: bytes,
        password_salt: bytes,
        pbkdf2_iter: int,
    ) -> int:
        """创建用户账号。

        参数:
        - username: 已规范化用户名，写入 user_account.username。
        - password_hash: PBKDF2-HMAC-SHA256 派生出的 32 字节密码摘要。
        - password_salt: PBKDF2 salt。
        - pbkdf2_iter: PBKDF2 迭代次数。

        返回:
        - 新用户的 user_id。

        数据库副作用:
        - INSERT user_account。
        - status、login_gen、created_at、updated_at 使用表默认值。

        异常:
        - 用户名唯一索引冲突时抛出 PyMySQL IntegrityError，由调用者转换成
          USERNAME_EXISTS。
        """

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_account
                    (username, password_hash, password_salt, pbkdf2_iter)
                VALUES (%s, %s, %s, %s)
                """,
                (username, password_hash, password_salt, pbkdf2_iter),
            )
            return int(cur.lastrowid)

    def increment_login_gen_for_login(self, conn: Any, *, user_id: int) -> int:
        """登录成功后递增 login_gen 并记录最近登录时间。

        参数:
        - conn: 外部事务连接。
        - user_id: user_account 主键。

        返回:
        - 递增后的 login_gen。

        数据库副作用:
        - UPDATE user_account.login_gen = login_gen + 1。
        - UPDATE user_account.last_login_at = 当前 UTC 时间。
        - updated_at 由表的 ON UPDATE CURRENT_TIMESTAMP(3) 自动刷新。
        """

        now = datetime.utcnow()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_account
                SET login_gen = login_gen + 1,
                    last_login_at = %s
                WHERE user_id = %s
                """,
                (now, user_id),
            )
            cur.execute(
                "SELECT login_gen FROM user_account WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise DatabaseError("user disappeared while incrementing login_gen")
        return int(row["login_gen"])

    def update_password_and_increment_login_gen(
        self,
        conn: Any,
        *,
        user_id: int,
        password_hash: bytes,
        password_salt: bytes,
        pbkdf2_iter: int,
    ) -> int:
        """改密成功后更新密码材料并递增 login_gen。

        参数:
        - user_id: user_account 主键。
        - password_hash/password_salt/pbkdf2_iter: 新密码的 PBKDF2 材料。

        返回:
        - 递增后的 login_gen。

        数据库副作用:
        - UPDATE user_account.password_hash。
        - UPDATE user_account.password_salt。
        - UPDATE user_account.pbkdf2_iter。
        - UPDATE user_account.login_gen = login_gen + 1。
        - updated_at 自动刷新。
        """

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_account
                SET password_hash = %s,
                    password_salt = %s,
                    pbkdf2_iter = %s,
                    login_gen = login_gen + 1
                WHERE user_id = %s
                """,
                (password_hash, password_salt, pbkdf2_iter, user_id),
            )
            cur.execute(
                "SELECT login_gen FROM user_account WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise DatabaseError("user disappeared while updating password")
        return int(row["login_gen"])

    def record_security_event(
        self,
        conn: Any,
        *,
        user_id: Optional[int],
        username: Optional[str],
        event_type: str,
        result: bool,
        client_id: Optional[str],
        remote_addr: Optional[str],
        reason: Optional[str],
    ) -> None:
        """写入 security_event_log。

        参数:
        - user_id: 可为空。用户名不存在、非法请求等场景没有可关联用户。
        - username: 用户名快照，可为空。
        - event_type: REGISTER、LOGIN_SUCCESS、LOGIN_FAIL、CHANGE_PASSWORD、
          TICKET_EXPIRED、REPLAY_BLOCKED 等机器可读类型。
        - result: True 写 1，False 写 0。
        - client_id: 报文 clientId，超过 64 字符会截断以匹配表字段。
        - remote_addr: WebSocket 远端地址，超过 128 字符会截断。
        - reason: 失败原因或安全原因，超过 128 字符会截断。

        返回:
        - None。

        数据库副作用:
        - INSERT security_event_log。
        - 不自动提交事务。
        """

        event = SecurityEvent(
            user_id=user_id,
            username=_truncate(username, 64),
            event_type=_truncate(event_type, 32) or event_type,
            result=1 if result else 0,
            client_id=_truncate(client_id, 64),
            remote_addr=_truncate(remote_addr, 128),
            reason=_truncate(reason, 128),
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO security_event_log
                    (user_id, username, event_type, result,
                     client_id, remote_addr, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event.user_id,
                    event.username,
                    event.event_type,
                    event.result,
                    event.client_id,
                    event.remote_addr,
                    event.reason,
                ),
            )

    def is_duplicate_username_error(self, exc: BaseException) -> bool:
        """判断异常是否来自用户名唯一索引冲突。

        参数:
        - exc: PyMySQL 抛出的异常对象。

        返回:
        - True: MySQL 错误码 1062，且通常表示 uk_user_account_username 冲突。
        - False: 其他数据库错误。
        """

        if pymysql is None:
            return False
        integrity_error = getattr(pymysql, "err").IntegrityError
        if not isinstance(exc, integrity_error):
            return False
        return bool(getattr(exc, "args", None)) and exc.args[0] == 1062


def _truncate(value: Optional[str], limit: int) -> Optional[str]:
    """把审计文本截断到表字段允许长度。

    参数:
    - value: 原始文本，可为空。
    - limit: 最大字符数。

    返回:
    - None 或截断后的字符串。
    """

    if value is None:
        return None
    return value[:limit]
