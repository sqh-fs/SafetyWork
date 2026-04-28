"""AS 认证服务器入口。

本文件实现 Kerberos 风格链路中的 AS（Authentication Server）。

AS 的职责：
- 接收 REGISTER_REQ，创建用户账号。
- 接收 AS_REQ，校验用户名密码，签发 TGT 和 KcTgs。
- 接收 CHANGE_PASSWORD_REQ，校验旧密码并更新密码。
- 写入登录审计和 TGT 签发日志。

主要输入：
- WebSocket JSON 文本帧。
- 客户端 RSA 加密后的 payload。
- MySQL AuthDB 中的用户、服务和密钥配置。

主要输出：
- REGISTER_REP、AS_REP、CHANGE_PASSWORD_REP 或 ERROR。
- user_account、login_audit、ticket_issue_log 等表的持久化变更。

安全边界：
- 客户端只能拿到 TGT 密文，不能解开 TGT。
- TGT 使用 K_TGS 加密，只有后续 TGS 服务能验证。
- AS_REP.payload.part 使用 Kuser 加密，只有知道用户密码的一方能解开。
"""

import asyncio
from datetime import datetime
import time
from typing import Any, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from config import ConfigError, load_as_config, load_db_config
from crypto_utils import (
    CryptoError,
    b64encode,
    decrypt_key_material,
    derive_kuser,
    derive_password_material,
    des_encrypt_object,
    generate_des_key,
    generate_salt,
    normalize_username,
    rsa_decrypt_object,
    ticket_hash,
    validate_password_policy,
    verify_password_hash,
)
from db import AuthDb, DbError
from protocol import (
    ProtocolError,
    TYPE_AS_REP,
    TYPE_AS_REQ,
    TYPE_CHANGE_PASSWORD_REP,
    TYPE_CHANGE_PASSWORD_REQ,
    TYPE_REGISTER_REP,
    TYPE_REGISTER_REQ,
    SUPPORTED_AS_TYPES,
    loads_json,
    make_error,
    make_message,
    make_payload,
    require_fields,
    require_string_field,
)


class AsRequestError(RuntimeError):
    """AS 业务处理错误。

    输入：
    - error_code：机器可读错误码，例如 BAD_CREDENTIALS、WEAK_PASSWORD。

    输出：
    - handle_message 捕获后转换成 ERROR 报文返回客户端。
    """

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def now_ms() -> int:
    """返回当前 Unix 毫秒时间戳。

    输出：
    - int：当前时间，单位毫秒。

    用途：
    - TGT 的 iat 和 exp 字段。
    """

    return int(time.time() * 1000)


def datetime_from_ms(ms: int) -> datetime:
    """把 Unix 毫秒时间戳转换成 UTC datetime。

    输入：
    - ms：毫秒时间戳。

    输出：
    - datetime：用于写入 MySQL DATETIME(3) 字段。
    """

    return datetime.utcfromtimestamp(ms / 1000.0)


def _bytes(value: Any) -> bytes:
    """把数据库返回的二进制字段统一转成 bytes。

    输入：
    - value：可能是 bytes、bytearray 或 memoryview。

    输出：
    - bytes：后续加密/解密函数可直接使用的字节。

    异常：
    - INVALID_DB_BYTES：字段类型不是预期的二进制类型。
    """

    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    raise AsRequestError("INVALID_DB_BYTES")


class AsServer:
    """AS WebSocket 服务器。

    生命周期：
    1. 构造时读取环境变量，创建 AuthDb 和 AsConfig。
    2. run() 启动前 ping 数据库并加载长期密钥。
    3. 每个 WebSocket 文本帧进入 handle_message 分发。

    内部状态：
    - as_private_pem：AS RSA 私钥，用于解密客户端敏感请求。
    - k_tgs：TGS 长期 DES 密钥，用于签发 TGT。
    - tgs_service_id：TGT 票据日志绑定的目标服务 ID。
    """

    def __init__(self) -> None:
        """初始化 AS 服务对象。

        输入：
        - 环境变量中的数据库配置、AS 配置和 AUTH_MASTER_KEY。

        输出：
        - AsServer 实例。

        注意：
        - 这里还不会连接数据库，也不会加载密钥；真实启动动作在 run() 中完成。
        """

        self.db = AuthDb(load_db_config())
        self.config = load_as_config(require_master_key=True)
        self.as_private_pem: Optional[bytes] = None
        self.k_tgs: Optional[bytes] = None
        self.tgs_service_id: Optional[int] = None

    def load_runtime_keys(self) -> None:
        """从数据库加载 AS 运行所需长期密钥。

        输入：
        - service_key 表中的 AS_RSA_PRIVATE 和 K_TGS 密文。
        - AUTH_MASTER_KEY，用于 Fernet 解密。

        输出：
        - 更新 self.as_private_pem、self.k_tgs、self.tgs_service_id。

        读取表：
        - service_registry。
        - service_key。

        异常：
        - ConfigError：密钥不存在或 K_TGS 不是 8 字节。
        - CryptoError：AUTH_MASTER_KEY 无法解开密钥密文。
        """

        with self.db.connection() as conn:
            as_key_row = self.db.get_service_key(
                conn,
                self.config.as_service_name,
                "AS_RSA_PRIVATE",
                self.config.as_key_version,
            )
            if as_key_row is None:
                raise ConfigError("AS_RSA_PRIVATE key is not configured")

            tgs_key_row = self.db.get_service_key(
                conn,
                self.config.tgs_service_name,
                "K_TGS",
                self.config.tgs_key_version,
            )
            if tgs_key_row is None:
                raise ConfigError("K_TGS key is not configured")

            self.as_private_pem = decrypt_key_material(
                self.config.auth_master_key,
                _bytes(as_key_row["key_ciphertext"]),
            )
            self.k_tgs = decrypt_key_material(
                self.config.auth_master_key,
                _bytes(tgs_key_row["key_ciphertext"]),
            )
            if len(self.k_tgs) != 8:
                raise ConfigError("K_TGS must be an 8-byte DES key")

            self.tgs_service_id = int(tgs_key_row["service_id"])
            conn.rollback()

    async def run(self) -> None:
        """启动 AS WebSocket 服务。

        输入：
        - 配置中的 AS_HOST / AS_PORT。

        输出：
        - 进程持续监听 WebSocket 连接。

        启动前检查：
        - 数据库可连接。
        - 运行密钥可加载。
        """

        self.db.ping()
        self.load_runtime_keys()

        print("=" * 72)
        print(f"[AS] WebSocket listening on ws://{self.config.host}:{self.config.port}")
        print(f"[AS] realm={self.config.realm}")
        print(f"[AS] as_service={self.config.as_service_name}")
        print(f"[AS] tgs_service={self.config.tgs_service_name}")
        print("=" * 72)

        async with websockets.serve(self.handle_client, self.config.host, self.config.port):
            await asyncio.Future()

    async def handle_client(self, websocket: Any) -> None:
        """处理单个 WebSocket 连接。

        输入：
        - websocket：websockets 库提供的连接对象。

        输出：
        - 无直接返回；每收到一条消息就调用 handle_message 并发送响应。

        连接关闭：
        - 只记录日志，不维护业务会话状态，因为 AS 是请求/响应式短流程。
        """

        remote = getattr(websocket, "remote_address", None)
        print(f"[AS CONNECT] remote={remote}")

        try:
            async for raw_message in websocket:
                await self.handle_message(websocket, raw_message)
        except ConnectionClosed as close_info:
            print(
                f"[AS CLOSED] remote={remote} "
                f"code={close_info.code} reason={close_info.reason}"
            )

    async def handle_message(self, websocket: Any, raw_message: Any) -> None:
        """解析并分发一条 AS 协议消息。

        输入：
        - websocket：当前连接，用于发送响应。
        - raw_message：客户端发来的 str 或 bytes 文本帧。

        输出：
        - 通过 websocket.send 返回成功响应或 ERROR。

        支持类型：
        - REGISTER_REQ
        - AS_REQ
        - CHANGE_PASSWORD_REQ

        错误处理：
        - 协议错误、业务错误、加密错误都会转换成 ERROR。
        - 未预期异常会记录服务端日志并返回 INTERNAL_ERROR。
        """

        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")

            msg = loads_json(raw_message)
            msg_type = require_string_field(msg, "type")

            if msg_type not in SUPPORTED_AS_TYPES:
                raise AsRequestError("UNSUPPORTED_TYPE")

            if msg_type == TYPE_REGISTER_REQ:
                response = self.handle_register_req(websocket, msg)
            elif msg_type == TYPE_AS_REQ:
                response = self.handle_as_req(websocket, msg)
            elif msg_type == TYPE_CHANGE_PASSWORD_REQ:
                response = self.handle_change_password_req(websocket, msg)
            else:
                raise AsRequestError("UNSUPPORTED_TYPE")

            await websocket.send(response)
        except ProtocolError as exc:
            await websocket.send(make_error(exc.error_code))
        except AsRequestError as exc:
            await websocket.send(make_error(exc.error_code))
        except CryptoError as exc:
            await websocket.send(make_error(str(exc)))
        except DbError as exc:
            print(f"[AS DB ERROR] {exc}")
            await websocket.send(make_error("DB_ERROR"))
        except Exception as exc:
            print(f"[AS INTERNAL ERROR] {type(exc).__name__}: {exc}")
            await websocket.send(make_error("INTERNAL_ERROR"))

    def decrypt_sensitive_payload(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """解密认证阶段的敏感 payload。

        输入：
        - msg：顶层协议对象，必须包含 payload。

        输出：
        - dict：RSA 解密后的 JSON 对象。

        使用密钥：
        - self.as_private_pem。

        异常：
        - KEY_NOT_CONFIGURED：AS 私钥未加载。
        - RSA_DECRYPT_FAILED：payload 不是合法 RSA-OAEP 密文。
        """

        if self.as_private_pem is None:
            raise AsRequestError("KEY_NOT_CONFIGURED")
        payload = require_string_field(msg, "payload")
        return rsa_decrypt_object(self.as_private_pem, payload)

    def remote_ip(self, websocket: Any) -> Optional[str]:
        """读取客户端 IP，用于审计日志。

        输入：
        - websocket：当前连接。

        输出：
        - str：客户端 IP。
        - None：无法从连接对象取得地址。
        """

        remote = getattr(websocket, "remote_address", None)
        if isinstance(remote, tuple) and len(remote) > 0:
            return str(remote[0])
        return None

    def validate_username(self, username: str) -> str:
        """规范化并校验用户名。

        输入：
        - username：客户端提交的用户名。

        输出：
        - str：trim + lower 后的用户名。

        异常：
        - INVALID_USERNAME：用户名为空或超过 64 字符。
        """

        normalized = normalize_username(username)
        if normalized == "" or len(normalized) > 64:
            raise AsRequestError("INVALID_USERNAME")
        return normalized

    def handle_register_req(self, websocket: Any, msg: Dict[str, Any]) -> str:
        """处理 REGISTER_REQ 注册请求。

        输入顶层字段：
        - type="REGISTER_REQ"
        - clientId：客户端实例 ID。
        - payload：Base64(RSA-OAEP-SHA256({"username","password"}))。

        输出：
        - REGISTER_REP，payload 为 {"ok":true,"userId":...} 的 JSON 字符串。
        - 或 ERROR，例如 WEAK_PASSWORD、USERNAME_EXISTS。

        数据库副作用：
        - 成功时写 user_account 和 login_audit。
        - 失败时尽量写 login_audit 记录失败原因。
        """

        require_fields(msg, ("clientId", "payload"))
        client_id = require_string_field(msg, "clientId")
        # 注册材料包含密码，必须先用 AS 私钥解开 RSA-OAEP 密文。
        plain = self.decrypt_sensitive_payload(msg)
        username = self.validate_username(require_string_field(plain, "username"))
        password = require_string_field(plain, "password")

        with self.db.connection() as conn:
            try:
                # 密码策略在入库前校验，弱密码不会生成 user_account 记录。
                if not validate_password_policy(password):
                    self.db.record_audit(
                        conn,
                        user_id=None,
                        username=username,
                        client_id=client_id,
                        event_type="REGISTER",
                        success=False,
                        error_code="WEAK_PASSWORD",
                        login_gen_after=None,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("WEAK_PASSWORD")

                # 用户名已经统一小写，数据库唯一索引负责兜底防重复。
                if self.db.find_user(conn, username) is not None:
                    self.db.record_audit(
                        conn,
                        user_id=None,
                        username=username,
                        client_id=client_id,
                        event_type="REGISTER",
                        success=False,
                        error_code="USERNAME_EXISTS",
                        login_gen_after=None,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("USERNAME_EXISTS")

                # user_account 不保存明文密码，只保存 PBKDF2 摘要和 salt。
                salt = generate_salt()
                password_hash = derive_password_material(
                    password,
                    salt,
                    self.config.pbkdf2_iter,
                )
                user_id = self.db.create_user(
                    conn,
                    username,
                    password_hash,
                    salt,
                    self.config.pbkdf2_iter,
                )
                self.db.record_audit(
                    conn,
                    user_id=user_id,
                    username=username,
                    client_id=client_id,
                    event_type="REGISTER",
                    success=True,
                    error_code=None,
                    login_gen_after=0,
                    ip_addr=self.remote_ip(websocket),
                )
                conn.commit()
            except Exception:
                # 注册流程中任意一步失败，都撤销本事务内已写入的数据。
                conn.rollback()
                raise

        return make_message(
            TYPE_REGISTER_REP,
            payload=make_payload({"ok": True, "userId": user_id}),
        )

    def handle_as_req(self, websocket: Any, msg: Dict[str, Any]) -> str:
        """处理 AS_REQ 登录请求并签发 TGT。

        输入顶层字段：
        - type="AS_REQ"
        - clientId：客户端实例 ID。
        - payload：Base64(RSA-OAEP-SHA256({"username","password","nonce"}))。

        输出：
        - AS_REP：
          - ticket：Base64(DES(K_TGS, TGT明文JSON))。
          - payload：普通 JSON 字符串，包含 salt、iter、part。
          - part：Base64(DES(Kuser, {"nonce","kcTgs","exp","loginGen"...}))。
        - 或 ERROR，例如 BAD_CREDENTIALS、ACCOUNT_DISABLED。

        数据库副作用：
        - 成功时递增 user_account.login_gen。
        - 成功时写 ticket_issue_log 和 LOGIN_SUCCESS 审计。
        - 失败时写 LOGIN_FAILED 审计。
        """

        require_fields(msg, ("clientId", "payload"))
        if self.k_tgs is None or self.tgs_service_id is None:
            raise AsRequestError("KEY_NOT_CONFIGURED")

        client_id = require_string_field(msg, "clientId")
        # 登录材料也包含密码，传输层必须经过 RSA 公钥加密。
        plain = self.decrypt_sensitive_payload(msg)
        username = self.validate_username(require_string_field(plain, "username"))
        password = require_string_field(plain, "password")
        nonce = require_string_field(plain, "nonce")

        issued_ms = now_ms()
        exp_ms = issued_ms + self.config.tgt_ttl_seconds * 1000
        issued_at = datetime_from_ms(issued_ms)
        expire_at = datetime_from_ms(exp_ms)
        kc_tgs = generate_des_key()

        with self.db.connection() as conn:
            try:
                # 登录成功会更新 loginGen，所以先锁定用户行避免并发登录写乱。
                user = self.db.find_user(conn, username, for_update=True)
                if user is None:
                    self.db.record_audit(
                        conn,
                        user_id=None,
                        username=username,
                        client_id=client_id,
                        event_type="LOGIN_FAILED",
                        success=False,
                        error_code="BAD_CREDENTIALS",
                        login_gen_after=None,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("BAD_CREDENTIALS")

                user_id = int(user["user_id"])
                login_gen_before = int(user["login_gen"])
                # 账号状态不是 ACTIVE 时，不允许签发新 TGT。
                if str(user["status"]).upper() != "ACTIVE":
                    self.db.record_audit(
                        conn,
                        user_id=user_id,
                        username=username,
                        client_id=client_id,
                        event_type="LOGIN_FAILED",
                        success=False,
                        error_code="ACCOUNT_DISABLED",
                        login_gen_after=login_gen_before,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("ACCOUNT_DISABLED")

                # 密码校验只比较 PBKDF2 摘要，不读取或保存明文密码。
                if not verify_password_hash(
                    password,
                    _bytes(user["password_salt"]),
                    int(user["pbkdf2_iter"]),
                    _bytes(user["password_hash"]),
                ):
                    self.db.record_audit(
                        conn,
                        user_id=user_id,
                        username=username,
                        client_id=client_id,
                        event_type="LOGIN_FAILED",
                        success=False,
                        error_code="BAD_CREDENTIALS",
                        login_gen_after=login_gen_before,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("BAD_CREDENTIALS")

                # 每次成功登录递增 loginGen，使旧票据在 TGS/GS 校验时失效。
                login_gen = self.db.increment_login_gen_for_login(
                    conn,
                    user_id=user_id,
                    client_id=client_id,
                )

                # TGT 明文只给 TGS 读取，客户端拿到的是 K_TGS 加密后的密文。
                tgt_plain = {
                    "ticketType": "TGT",
                    "realm": self.config.realm,
                    "userId": user_id,
                    "username": username,
                    "clientId": client_id,
                    "service": self.config.tgs_service_name,
                    "kcTgs": b64encode(kc_tgs),
                    "loginGen": login_gen,
                    "iat": issued_ms,
                    "exp": exp_ms,
                }
                tgt = des_encrypt_object(self.k_tgs, tgt_plain)

                # part 由用户密码派生出的 Kuser 加密，客户端验证 nonce 后才接受登录结果。
                kuser = derive_kuser(
                    password,
                    _bytes(user["password_salt"]),
                    int(user["pbkdf2_iter"]),
                )
                part = des_encrypt_object(
                    kuser,
                    {
                        "userId": user_id,
                        "username": username,
                        "nonce": nonce,
                        "kcTgs": b64encode(kc_tgs),
                        "exp": exp_ms,
                        "loginGen": login_gen,
                    },
                )

                # 审计只保存票据密文摘要，不保存 TGT 明文或 KcTgs 明文。
                self.db.record_ticket_issue(
                    conn,
                    user_id=user_id,
                    client_id=client_id,
                    ticket_type="TGT",
                    service_id=self.tgs_service_id,
                    ticket_hash_value=ticket_hash(tgt),
                    login_gen=login_gen,
                    issued_at=issued_at,
                    expire_at=expire_at,
                )
                self.db.record_audit(
                    conn,
                    user_id=user_id,
                    username=username,
                    client_id=client_id,
                    event_type="LOGIN_SUCCESS",
                    success=True,
                    error_code=None,
                    login_gen_after=login_gen,
                    ip_addr=self.remote_ip(websocket),
                )
                conn.commit()
            except Exception:
                # 登录流程涉及 loginGen、审计和票据日志，失败时整体回滚。
                conn.rollback()
                raise

        # salt/iter 必须返回给客户端，否则客户端无法派生 Kuser 解开 part。
        response_payload = {
            "salt": b64encode(_bytes(user["password_salt"])),
            "iter": int(user["pbkdf2_iter"]),
            "part": part,
        }
        return make_message(
            TYPE_AS_REP,
            ticket=tgt,
            payload=make_payload(response_payload),
        )

    def handle_change_password_req(self, websocket: Any, msg: Dict[str, Any]) -> str:
        """处理 CHANGE_PASSWORD_REQ 改密请求。

        输入顶层字段：
        - type="CHANGE_PASSWORD_REQ"
        - clientId：客户端实例 ID。
        - payload：Base64(RSA-OAEP-SHA256({"username","oldPassword","newPassword"}))。

        输出：
        - CHANGE_PASSWORD_REP，payload 为 {"ok":true}。
        - 或 ERROR，例如 BAD_CREDENTIALS、WEAK_PASSWORD、ACCOUNT_DISABLED。

        数据库副作用：
        - 成功时更新 password_hash、password_salt、pbkdf2_iter。
        - 成功时递增 loginGen，使旧票据和旧 GS 会话失效。
        - 成功或失败都写 CHANGE_PASSWORD 审计。
        """

        require_fields(msg, ("clientId", "payload"))
        client_id = require_string_field(msg, "clientId")
        # 改密同时包含旧密码和新密码，必须通过 AS 私钥解密 RSA payload。
        plain = self.decrypt_sensitive_payload(msg)
        username = self.validate_username(require_string_field(plain, "username"))
        old_password = require_string_field(plain, "oldPassword")
        new_password = require_string_field(plain, "newPassword")

        with self.db.connection() as conn:
            try:
                # 改密会更新密码和 loginGen，所以需要锁定用户行。
                user = self.db.find_user(conn, username, for_update=True)
                user_id = int(user["user_id"]) if user is not None else None
                login_gen_before = int(user["login_gen"]) if user is not None else None

                # 新密码不满足复杂度时直接拒绝，避免写入弱密码摘要。
                if not validate_password_policy(new_password):
                    self.db.record_audit(
                        conn,
                        user_id=user_id,
                        username=username,
                        client_id=client_id,
                        event_type="CHANGE_PASSWORD",
                        success=False,
                        error_code="WEAK_PASSWORD",
                        login_gen_after=login_gen_before,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("WEAK_PASSWORD")

                if user is None:
                    self.db.record_audit(
                        conn,
                        user_id=None,
                        username=username,
                        client_id=client_id,
                        event_type="CHANGE_PASSWORD",
                        success=False,
                        error_code="BAD_CREDENTIALS",
                        login_gen_after=None,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("BAD_CREDENTIALS")

                if str(user["status"]).upper() != "ACTIVE":
                    self.db.record_audit(
                        conn,
                        user_id=user_id,
                        username=username,
                        client_id=client_id,
                        event_type="CHANGE_PASSWORD",
                        success=False,
                        error_code="ACCOUNT_DISABLED",
                        login_gen_after=login_gen_before,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("ACCOUNT_DISABLED")

                # 旧密码必须正确，否则不能刷新密码和 loginGen。
                if not verify_password_hash(
                    old_password,
                    _bytes(user["password_salt"]),
                    int(user["pbkdf2_iter"]),
                    _bytes(user["password_hash"]),
                ):
                    self.db.record_audit(
                        conn,
                        user_id=user_id,
                        username=username,
                        client_id=client_id,
                        event_type="CHANGE_PASSWORD",
                        success=False,
                        error_code="BAD_CREDENTIALS",
                        login_gen_after=login_gen_before,
                        ip_addr=self.remote_ip(websocket),
                    )
                    conn.commit()
                    raise AsRequestError("BAD_CREDENTIALS")

                # 新密码重新生成 salt，避免复用旧密码派生参数。
                salt = generate_salt()
                password_hash = derive_password_material(
                    new_password,
                    salt,
                    self.config.pbkdf2_iter,
                )
                login_gen = self.db.update_password_and_increment_login_gen(
                    conn,
                    user_id=user_id,
                    password_hash=password_hash,
                    password_salt=salt,
                    pbkdf2_iter=self.config.pbkdf2_iter,
                )
                self.db.record_audit(
                    conn,
                    user_id=user_id,
                    username=username,
                    client_id=client_id,
                    event_type="CHANGE_PASSWORD",
                    success=True,
                    error_code=None,
                    login_gen_after=login_gen,
                    ip_addr=self.remote_ip(websocket),
                )
                conn.commit()
            except Exception:
                # 改密涉及账号和审计，失败时回滚本事务内所有写入。
                conn.rollback()
                raise

        return make_message(
            TYPE_CHANGE_PASSWORD_REP,
            payload=make_payload({"ok": True}),
        )


def main() -> None:
    """命令行入口。

    输入：
    - 环境变量中的数据库、密钥和端口配置。

    输出：
    - 启动 AS WebSocket 服务；启动失败时返回非 0 退出码。
    """

    try:
        server = AsServer()
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[AS] stopped")
    except ConfigError as exc:
        print(f"[AS CONFIG ERROR] {exc}")
        raise SystemExit(2)
    except CryptoError as exc:
        print(f"[AS CRYPTO ERROR] {exc}")
        raise SystemExit(2)
    except DbError as exc:
        print(f"[AS DB ERROR] {exc}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
