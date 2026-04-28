"""AS 认证数据库访问层。

本文件封装 MySQL 表访问，业务层通过 AuthDb 调用数据库，不直接写 SQL。

重要设计：
- connection() 默认 autocommit=False，事务提交/回滚由调用方控制。
- DAO 方法只执行 SQL，不决定业务成功失败的协议响应。
- 所有时间使用 UTC naive datetime 写入 DATETIME(3)，避免本地时区影响审计。

涉及表：
- user_account：用户账号、密码摘要、PBKDF2 参数、loginGen。
- service_registry：AS/TGS/GS 服务注册信息。
- service_key：长期密钥密文。
- login_audit：注册、登录、改密等审计记录。
- ticket_issue_log：TGT 和后续 Service Ticket 签发日志。
"""

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, Optional

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError as exc:  # pragma: no cover - exercised only when deps are missing.
    pymysql = None
    DictCursor = None
    _PYMYSQL_IMPORT_ERROR = exc
else:
    _PYMYSQL_IMPORT_ERROR = None

from config import DbConfig


class DbError(RuntimeError):
    """数据库访问层错误。

    典型原因：
    - pymysql 未安装。
    - upsert 后查不到 service_id。
    - 更新用户后查不到 login_gen。
    """

    pass


def utc_now_naive() -> datetime:
    """返回当前 UTC 时间。

    输出：
    - datetime：无时区对象，用于写入 MySQL DATETIME(3)。
    """

    return datetime.utcnow()


class AuthDb:
    """认证数据库访问对象。

    输入：
    - DbConfig：数据库连接参数。

    输出：
    - 各方法返回 dict、int 或 None，具体含义见方法 docstring。

    事务约定：
    - connection() 打开的连接不自动提交。
    - 调用方必须在业务成功后 conn.commit()，失败时 conn.rollback()。
    - 这样注册、审计、票据日志可以和业务变更保持同一事务边界。
    """

    def __init__(self, config: DbConfig) -> None:
        """保存数据库配置。

        输入：
        - config：MySQL 连接配置。
        """

        self.config = config

    def _require_pymysql(self) -> None:
        """确认 pymysql 依赖可用。

        输出：
        - None：依赖存在时返回。

        异常：
        - DbError：依赖未安装，提示安装 as/requirements.txt。
        """

        if pymysql is None:
            raise DbError(
                "pymysql is required for MySQL support; install as/requirements.txt"
            ) from _PYMYSQL_IMPORT_ERROR

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """创建 MySQL 连接上下文。

        输入：
        - 无，使用构造函数中的 DbConfig。

        输出：
        - Iterator[Connection]：yield 一个 pymysql connection。

        副作用：
        - 打开数据库连接，退出上下文时关闭连接。
        - 不自动 commit/rollback，事务由调用方控制。
        """

        self._require_pymysql()
        conn = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset=self.config.charset,
            cursorclass=DictCursor,
            autocommit=False,
        )
        try:
            yield conn
        finally:
            conn.close()

    def ping(self) -> None:
        """检查数据库连接是否可用。

        输入：
        - 无。

        输出：
        - None：连接成功。

        副作用：
        - 建立并关闭一次 MySQL 连接。
        """

        with self.connection() as conn:
            conn.ping(reconnect=False)

    def get_service(self, conn: Any, service_name: str) -> Optional[Dict[str, Any]]:
        """查询启用状态的服务注册记录。

        输入：
        - conn：外部传入的事务连接。
        - service_name：服务名，例如 as/GAME.LOCAL 或 krbtgt/GAME.LOCAL。

        输出：
        - dict：service_registry 行。
        - None：服务不存在或未启用。

        读取表：
        - service_registry。
        """

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM service_registry
                WHERE service_name = %s AND status = 'ENABLED'
                """,
                (service_name,),
            )
            return cur.fetchone()

    def upsert_service(
        self,
        conn: Any,
        service_name: str,
        service_type: str,
        realm: str,
        host: str,
        port: int,
        websocket_url: str,
    ) -> int:
        """插入或更新服务注册记录。

        输入：
        - conn：外部事务连接。
        - service_name / service_type / realm / host / port / websocket_url：
          服务注册字段。

        输出：
        - int：service_registry.service_id。

        写入表：
        - service_registry。

        事务：
        - 本方法不提交事务，由调用方 seed_auth_keys.py 统一 commit。
        """

        now = utc_now_naive()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO service_registry
                    (service_name, service_type, realm, host, port, websocket_url,
                     status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'ENABLED', %s, %s)
                ON DUPLICATE KEY UPDATE
                    service_type = VALUES(service_type),
                    realm = VALUES(realm),
                    host = VALUES(host),
                    port = VALUES(port),
                    websocket_url = VALUES(websocket_url),
                    status = 'ENABLED',
                    updated_at = VALUES(updated_at)
                """,
                (service_name, service_type, realm, host, port, websocket_url, now, now),
            )
            cur.execute(
                "SELECT service_id FROM service_registry WHERE service_name = %s",
                (service_name,),
            )
            row = cur.fetchone()
            if row is None:
                raise DbError("SERVICE_UPSERT_FAILED")
            return int(row["service_id"])

    def get_service_key(
        self,
        conn: Any,
        service_name: str,
        key_usage: str,
        key_version: str,
    ) -> Optional[Dict[str, Any]]:
        """读取启用状态的服务密钥。

        输入：
        - conn：外部事务连接。
        - service_name：服务名。
        - key_usage：密钥用途，例如 AS_RSA_PRIVATE、AS_RSA_PUBLIC、K_TGS。
        - key_version：密钥版本号，例如 v1。

        输出：
        - dict：service_key 与 service_registry join 后的记录。
        - None：密钥不存在、未启用或服务未启用。

        读取表：
        - service_key。
        - service_registry。
        """

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sk.*, sr.service_name, sr.service_type
                FROM service_key sk
                JOIN service_registry sr ON sr.service_id = sk.service_id
                WHERE sr.service_name = %s
                  AND sk.key_usage = %s
                  AND sk.key_version = %s
                  AND sk.enabled = 1
                  AND sr.status = 'ENABLED'
                """,
                (service_name, key_usage, key_version),
            )
            return cur.fetchone()

    def upsert_service_key(
        self,
        conn: Any,
        service_id: int,
        key_usage: str,
        key_version: str,
        algorithm: str,
        key_ciphertext: bytes,
    ) -> None:
        """插入或更新某个服务的密钥密文。

        输入：
        - service_id：service_registry 主键。
        - key_usage：密钥用途。
        - key_version：版本号。
        - algorithm：算法名，例如 RSA 或 DES。
        - key_ciphertext：Fernet 加密后的密钥材料。

        输出：
        - None。

        写入表：
        - service_key。

        事务：
        - 不提交事务，由调用方统一提交。
        """

        now = utc_now_naive()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO service_key
                    (service_id, key_usage, key_version, algorithm, key_ciphertext,
                     enabled, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                ON DUPLICATE KEY UPDATE
                    algorithm = VALUES(algorithm),
                    key_ciphertext = VALUES(key_ciphertext),
                    enabled = 1,
                    updated_at = VALUES(updated_at)
                """,
                (
                    service_id,
                    key_usage,
                    key_version,
                    algorithm,
                    key_ciphertext,
                    now,
                    now,
                ),
            )

    def find_user(
        self,
        conn: Any,
        username: str,
        for_update: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """按用户名查询用户账号。

        输入：
        - conn：外部事务连接。
        - username：已经 normalize 后的小写用户名。
        - for_update：是否追加 SELECT ... FOR UPDATE 锁定该用户行。

        输出：
        - dict：user_account 行。
        - None：用户不存在。

        读取表：
        - user_account。

        使用场景：
        - 登录和改密需要 for_update=True，防止并发修改 loginGen 或密码。
        """

        sql = "SELECT * FROM user_account WHERE username = %s"
        if for_update:
            sql += " FOR UPDATE"

        with conn.cursor() as cur:
            cur.execute(sql, (username,))
            return cur.fetchone()

    def create_user(
        self,
        conn: Any,
        username: str,
        password_hash: bytes,
        password_salt: bytes,
        pbkdf2_iter: int,
    ) -> int:
        """创建新用户账号。

        输入：
        - username：规范化后的用户名。
        - password_hash：PBKDF2 32 字节摘要。
        - password_salt：随机盐。
        - pbkdf2_iter：PBKDF2 迭代次数。

        输出：
        - int：新建用户的 user_id。

        写入表：
        - user_account。

        事务：
        - 不提交事务，注册成功时由 as_server.py 同时提交用户和审计记录。
        """

        now = utc_now_naive()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_account
                    (username, password_hash, password_salt, password_algo,
                     pbkdf2_iter, login_gen, status, created_at, updated_at)
                VALUES (%s, %s, %s, 'PBKDF2', %s, 0, 'ACTIVE', %s, %s)
                """,
                (username, password_hash, password_salt, pbkdf2_iter, now, now),
            )
            return int(cur.lastrowid)

    def increment_login_gen_for_login(
        self,
        conn: Any,
        user_id: int,
        client_id: str,
    ) -> int:
        """登录成功后递增 loginGen。

        输入：
        - user_id：用户主键。
        - client_id：本次客户端实例 ID。

        输出：
        - int：递增后的 login_gen。

        写入表：
        - user_account.login_gen、last_client_id、last_login_at、updated_at。

        作用：
        - 让旧 TGT、旧 Service Ticket 和旧 GS 会话在后续校验 loginGen 时失效。
        """

        now = utc_now_naive()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_account
                SET login_gen = login_gen + 1,
                    last_client_id = %s,
                    last_login_at = %s,
                    updated_at = %s
                WHERE user_id = %s
                """,
                (client_id, now, now, user_id),
            )
            cur.execute(
                "SELECT login_gen FROM user_account WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise DbError("USER_NOT_FOUND")
            return int(row["login_gen"])

    def update_password_and_increment_login_gen(
        self,
        conn: Any,
        user_id: int,
        password_hash: bytes,
        password_salt: bytes,
        pbkdf2_iter: int,
    ) -> int:
        """更新用户密码并递增 loginGen。

        输入：
        - user_id：用户主键。
        - password_hash / password_salt / pbkdf2_iter：新密码派生结果和参数。

        输出：
        - int：递增后的 login_gen。

        写入表：
        - user_account.password_hash、password_salt、pbkdf2_iter、login_gen、updated_at。

        作用：
        - 改密成功后强制旧票据和旧业务会话失效。
        """

        now = utc_now_naive()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_account
                SET password_hash = %s,
                    password_salt = %s,
                    pbkdf2_iter = %s,
                    login_gen = login_gen + 1,
                    updated_at = %s
                WHERE user_id = %s
                """,
                (password_hash, password_salt, pbkdf2_iter, now, user_id),
            )
            cur.execute(
                "SELECT login_gen FROM user_account WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise DbError("USER_NOT_FOUND")
            return int(row["login_gen"])

    def record_audit(
        self,
        conn: Any,
        user_id: Optional[int],
        username: Optional[str],
        client_id: Optional[str],
        event_type: str,
        success: bool,
        error_code: Optional[str],
        login_gen_after: Optional[int],
        ip_addr: Optional[str],
    ) -> None:
        """写入登录与安全审计日志。

        输入：
        - user_id：关联用户，登录失败且用户不存在时为 None。
        - username：事件发生时提交的用户名快照。
        - client_id：客户端实例 ID。
        - event_type：REGISTER、LOGIN_SUCCESS、LOGIN_FAILED、CHANGE_PASSWORD 等。
        - success：本次安全事件是否成功。
        - error_code：失败时的机器可读错误码。
        - login_gen_after：事件发生后的 loginGen，未知时为 None。
        - ip_addr：客户端 IP。

        输出：
        - None。

        写入表：
        - login_audit。
        """

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO login_audit
                    (user_id, username_snapshot, client_id, event_type, success,
                     error_code, ip_addr, login_gen_after, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    username,
                    client_id,
                    event_type,
                    1 if success else 0,
                    error_code,
                    ip_addr,
                    login_gen_after,
                    utc_now_naive(),
                ),
            )

    def record_ticket_issue(
        self,
        conn: Any,
        user_id: int,
        client_id: str,
        ticket_type: str,
        service_id: int,
        ticket_hash_value: str,
        login_gen: int,
        issued_at: datetime,
        expire_at: datetime,
    ) -> None:
        """记录票据签发日志。

        输入：
        - user_id / client_id：票据归属。
        - ticket_type：当前 AS 只写 TGT，后续 TGS 可写 SERVICE_TICKET。
        - service_id：票据目标服务，本轮 TGT 绑定 TGS 服务。
        - ticket_hash_value：票据密文 SHA-256 摘要。
        - login_gen：票据绑定的登录代数。
        - issued_at / expire_at：签发和过期时间。

        输出：
        - None。

        写入表：
        - ticket_issue_log。

        注意：
        - 这里只保存票据密文摘要，不保存明文票据内容。
        """

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ticket_issue_log
                    (user_id, client_id, ticket_type, service_id, ticket_hash,
                     login_gen, issued_at, expire_at, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ISSUED', %s)
                """,
                (
                    user_id,
                    client_id,
                    ticket_type,
                    service_id,
                    ticket_hash_value,
                    login_gen,
                    issued_at,
                    expire_at,
                    utc_now_naive(),
                ),
            )
