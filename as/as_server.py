"""AS 认证服务器主程序。

本文件实现 WebSocket JSON 协议入口，处理三类请求:
- REGISTER_REQ: 注册账号，写入 user_account，并记录 REGISTER 安全事件。
- AS_REQ: 校验用户名/密码，递增 login_gen，签发 TGT，并记录登录事件。
- CHANGE_PASSWORD_REQ: 校验旧密码后改密，递增 login_gen，并记录改密事件。

两表化后的持久化边界:
- 只读写 user_account 和 security_event_log。
- TGT 仍正常签发，但票据签发过程不再额外落表。

密钥来源:
- AS RSA 私钥从 AS_RSA_PRIVATE_PEM 或 AS_RSA_PRIVATE_KEY_PATH 加载。
- K_TGS 从 K_TGS_BASE64 加载，解码后必须是 8 字节 DES key。
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import serialization

try:
    import websockets
except ImportError as exc:  # pragma: no cover - only hit when dependencies missing.
    websockets = None
    _WEBSOCKETS_IMPORT_ERROR = exc
else:
    _WEBSOCKETS_IMPORT_ERROR = None

from config import ConfigError, load_as_config, load_db_config
from crypto_utils import (
    CryptoError,
    b64decode,
    b64encode,
    derive_kuser,
    derive_password_material,
    des_encrypt_object,
    generate_des_key,
    generate_salt,
    normalize_username,
    rsa_decrypt_object,
    validate_password_policy,
    verify_password_hash,
)
from db import AuthDao, DatabaseError
from protocol import (
    SUPPORTED_AS_TYPES,
    ProtocolError,
    TYPE_AS_REP,
    TYPE_AS_REQ,
    TYPE_CHANGE_PASSWORD_REP,
    TYPE_CHANGE_PASSWORD_REQ,
    TYPE_REGISTER_REP,
    TYPE_REGISTER_REQ,
    loads_json,
    make_error,
    make_message,
    make_payload,
    require_fields,
    require_string_field,
)


USERNAME_MAX_LENGTH = 64


class AsRequestError(RuntimeError):
    """业务错误，最终会转换成 ERROR 报文。

    参数:
    - error_code: 机器可读错误码，例如 BAD_CREDENTIALS、ACCOUNT_DISABLED。

    输出:
    - handle_message 捕获后返回 {"type":"ERROR","error":error_code}。
    """

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def now_ms() -> int:
    """返回当前 Unix 时间戳毫秒数。

    用途:
    - 写入 TGT 的 iat/exp 字段。
    - 写入 AS_REP.payload.part.exp 字段，便于客户端知道票据过期时间。
    """

    return int(time.time() * 1000)


def _bytes(value: Any) -> bytes:
    """把数据库返回的 VARBINARY 字段统一转成 bytes。

    参数:
    - value: PyMySQL 可能返回 bytes、bytearray 或 memoryview。

    返回:
    - bytes。
    """

    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    return bytes(value)


class AsServer:
    """AS WebSocket 服务对象。

    初始化输入:
    - 环境变量中的数据库配置、AS 配置和密钥来源。

    运行时状态:
    - as_private_pem: AS RSA 私钥 PEM 字节串，用于解密客户端 payload。
    - k_tgs: TGS 长期 DES key，用于加密 TGT。

    数据库副作用:
    - 注册写 user_account 和 security_event_log。
    - 登录更新 user_account.login_gen / last_login_at，并写 security_event_log。
    - 改密更新密码材料 / login_gen，并写 security_event_log。
    """

    def __init__(self) -> None:
        self.db_config = load_db_config()
        self.config = load_as_config()
        self.db = AuthDao(self.db_config)
        self.as_private_pem: Optional[bytes] = None
        self.k_tgs: Optional[bytes] = None

    def load_runtime_keys(self) -> None:
        """加载并校验 AS 运行所需长期密钥。

        输入:
        - AS_RSA_PRIVATE_PEM: PEM 文本。若通过命令行设置时包含字面量 "\\n"，
          这里会转换成真正换行。
        - AS_RSA_PRIVATE_KEY_PATH: 私钥 PEM 文件路径。仅在 AS_RSA_PRIVATE_PEM
          未设置时使用。
        - K_TGS_BASE64: Base64 文本，解码后必须正好是 8 字节 DES key。

        输出:
        - self.as_private_pem。
        - self.k_tgs。

        异常:
        - ConfigError: 缺少密钥、路径错误、私钥格式错误或 K_TGS 长度错误。
        - CryptoError: K_TGS 不是合法 Base64。
        """

        private_text = self.config.as_private_key_pem
        if private_text:
            if "\\n" in private_text:
                private_text = private_text.replace("\\n", "\n")
            private_pem = private_text.encode("utf-8")
        elif self.config.as_private_key_path:
            private_path = Path(self.config.as_private_key_path)
            if not private_path.exists():
                raise ConfigError(f"AS RSA private key file not found: {private_path}")
            private_pem = private_path.read_bytes()
        else:
            raise ConfigError(
                "AS_RSA_PRIVATE_PEM or AS_RSA_PRIVATE_KEY_PATH is required"
            )

        try:
            serialization.load_pem_private_key(private_pem, password=None)
        except Exception as exc:
            raise ConfigError("AS RSA private key PEM is invalid") from exc

        k_tgs = b64decode(self.config.k_tgs_base64)
        if len(k_tgs) != 8:
            raise ConfigError("K_TGS_BASE64 must decode to exactly 8 bytes")

        self.as_private_pem = private_pem
        self.k_tgs = k_tgs

    async def run(self) -> None:
        """启动 AS WebSocket 服务。

        前置条件:
        - MySQL 已按 as/schema_auth.sql 初始化两张表。
        - 已设置 AS RSA 私钥和 K_TGS 环境变量。
        - 已安装 as/requirements.txt 中的依赖。

        输出:
        - 持续监听 self.config.host:self.config.port。
        """

        if websockets is None:
            raise ConfigError(
                "websockets is required; install dependencies from as/requirements.txt"
            ) from _WEBSOCKETS_IMPORT_ERROR

        self.db.ping()
        self.load_runtime_keys()
        print(
            f"AS server listening on ws://{self.config.host}:{self.config.port} "
            f"realm={self.config.realm}"
        )
        async with websockets.serve(
            self.handle_socket,
            self.config.host,
            self.config.port,
        ):
            await asyncio.Future()

    async def handle_socket(self, websocket: Any, path: Optional[str] = None) -> None:
        """处理单个 WebSocket 连接。

        参数:
        - websocket: websockets 库传入的连接对象。
        - path: 旧版 websockets 会传入路径；新版可能不传，本服务不依赖它。

        输入:
        - 客户端逐条发送 UTF-8 JSON 字符串。

        输出:
        - 对每条请求返回一条 JSON 字符串。
        """

        async for raw in websocket:
            response = self.handle_message(websocket, raw)
            await websocket.send(response)

    def handle_message(self, websocket: Any, raw: str) -> str:
        """解析并路由一条 AS 协议消息。

        参数:
        - websocket: 当前连接，用于提取 remote_addr 写审计。
        - raw: 客户端发来的 JSON 字符串。

        返回:
        - REGISTER_REP、AS_REP、CHANGE_PASSWORD_REP 或 ERROR 的 JSON 字符串。
        """

        try:
            msg = loads_json(raw)
            msg_type = msg.get("type")
            if msg_type not in SUPPORTED_AS_TYPES:
                raise ProtocolError("UNSUPPORTED_TYPE")
            if msg_type == TYPE_REGISTER_REQ:
                return self.handle_register_req(websocket, msg)
            if msg_type == TYPE_AS_REQ:
                return self.handle_as_req(websocket, msg)
            if msg_type == TYPE_CHANGE_PASSWORD_REQ:
                return self.handle_change_password_req(websocket, msg)
            raise ProtocolError("UNSUPPORTED_TYPE")
        except ProtocolError as exc:
            return make_error(exc.error_code)
        except AsRequestError as exc:
            return make_error(exc.error_code)
        except CryptoError as exc:
            return make_error(str(exc))
        except Exception as exc:
            print(f"AS internal error: {exc}", file=sys.stderr)
            return make_error("INTERNAL_ERROR")

    def decrypt_sensitive_payload(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """解密 REGISTER_REQ / AS_REQ / CHANGE_PASSWORD_REQ 的 payload。

        参数:
        - msg: 顶层协议消息，必须包含 payload 字段。

        返回:
        - RSA-OAEP-SHA256 解密后的 JSON 对象。

        异常:
        - KEY_NOT_CONFIGURED: AS 私钥尚未加载。
        - ProtocolError: payload 缺失或类型不正确。
        - CryptoError: Base64 或 RSA 解密失败。
        """

        require_fields(msg, ("payload",))
        payload = msg.get("payload")
        if not isinstance(payload, str):
            raise ProtocolError("INVALID_PAYLOAD")
        if self.as_private_pem is None:
            raise AsRequestError("KEY_NOT_CONFIGURED")
        return rsa_decrypt_object(self.as_private_pem, payload)

    def validate_username(self, username: str) -> str:
        """规范化并校验用户名。

        参数:
        - username: 客户端输入的用户名。

        返回:
        - trim + lower 后的用户名。

        异常:
        - INVALID_USERNAME: 规范化后为空或超过 user_account.username 的 64 字符限制。
        """

        normalized = normalize_username(username)
        if normalized == "" or len(normalized) > USERNAME_MAX_LENGTH:
            raise AsRequestError("INVALID_USERNAME")
        return normalized

    def remote_ip(self, websocket: Any) -> Optional[str]:
        """提取 WebSocket 远端地址。

        参数:
        - websocket: websockets 连接对象。

        返回:
        - "host:port" 字符串；无法获取时返回 None。

        用途:
        - 写入 security_event_log.remote_addr，仅用于审计和排查。
        """

        remote = getattr(websocket, "remote_address", None)
        if remote is None:
            return None
        if isinstance(remote, tuple):
            if len(remote) >= 2:
                return f"{remote[0]}:{remote[1]}"
            if len(remote) == 1:
                return str(remote[0])
        return str(remote)

    def record_event(
        self,
        conn: Any,
        websocket: Any,
        *,
        user_id: Optional[int],
        username: Optional[str],
        event_type: str,
        result: bool,
        client_id: Optional[str],
        reason: Optional[str] = None,
    ) -> None:
        """写入一条 security_event_log。

        参数:
        - conn: 当前事务连接。
        - websocket: 当前连接，用于获取远端地址。
        - user_id / username / event_type / result / client_id / reason:
          对应 security_event_log 表字段。

        返回:
        - None。
        """

        self.db.record_security_event(
            conn,
            user_id=user_id,
            username=username,
            event_type=event_type,
            result=result,
            client_id=client_id,
            remote_addr=self.remote_ip(websocket),
            reason=reason,
        )

    def handle_register_req(self, websocket: Any, msg: Dict[str, Any]) -> str:
        """处理 REGISTER_REQ 注册请求。

        输入报文:
        - type="REGISTER_REQ"。
        - clientId: 客户端运行期实例 ID。
        - payload: Base64(RSA-OAEP-SHA256({"username","password"}))。

        输出报文:
        - REGISTER_REP.payload: 普通 JSON 字符串 {"ok":true,"userId":...}。
        - 失败时返回 ERROR，例如 WEAK_PASSWORD、USERNAME_EXISTS。

        数据库副作用:
        - 成功时 INSERT user_account。
        - 成功和失败都会尽量 INSERT security_event_log，event_type=REGISTER。
        """

        require_fields(msg, ("clientId", "payload"))
        client_id = require_string_field(msg, "clientId")
        plain = self.decrypt_sensitive_payload(msg)
        username = self.validate_username(require_string_field(plain, "username"))
        password = require_string_field(plain, "password")

        with self.db.connection() as conn:
            try:
                if not validate_password_policy(password):
                    self.record_event(
                        conn,
                        websocket,
                        user_id=None,
                        username=username,
                        event_type="REGISTER",
                        result=False,
                        client_id=client_id,
                        reason="WEAK_PASSWORD",
                    )
                    conn.commit()
                    raise AsRequestError("WEAK_PASSWORD")

                salt = generate_salt()
                password_hash = derive_password_material(
                    password,
                    salt,
                    self.config.pbkdf2_iter,
                )
                try:
                    user_id = self.db.create_user(
                        conn,
                        username=username,
                        password_hash=password_hash,
                        password_salt=salt,
                        pbkdf2_iter=self.config.pbkdf2_iter,
                    )
                except Exception as exc:
                    if self.db.is_duplicate_username_error(exc):
                        conn.rollback()
                        self.record_event(
                            conn,
                            websocket,
                            user_id=None,
                            username=username,
                            event_type="REGISTER",
                            result=False,
                            client_id=client_id,
                            reason="USERNAME_EXISTS",
                        )
                        conn.commit()
                        raise AsRequestError("USERNAME_EXISTS") from exc
                    raise

                self.record_event(
                    conn,
                    websocket,
                    user_id=user_id,
                    username=username,
                    event_type="REGISTER",
                    result=True,
                    client_id=client_id,
                )
                conn.commit()
            except AsRequestError:
                raise
            except Exception:
                conn.rollback()
                raise

        return make_message(
            TYPE_REGISTER_REP,
            payload=make_payload({"ok": True, "userId": user_id}),
        )

    def handle_as_req(self, websocket: Any, msg: Dict[str, Any]) -> str:
        """处理 AS_REQ 登录并签发 TGT。

        输入报文:
        - type="AS_REQ"。
        - clientId: 客户端运行期实例 ID。
        - payload: Base64(RSA-OAEP-SHA256({"username","password","nonce"}))。

        输出报文:
        - AS_REP.ticket: Base64(DES-CBC-PKCS7(K_TGS, TGT_JSON))。
        - AS_REP.payload: 普通 JSON 字符串，包含 salt、iter、part。
        - payload.part: Base64(DES-CBC-PKCS7(Kuser, part_JSON))。

        数据库副作用:
        - 成功时 UPDATE user_account.login_gen 和 last_login_at。
        - 成功记录 LOGIN_SUCCESS。
        - 失败记录 LOGIN_FAIL。
        """

        require_fields(msg, ("clientId", "payload"))
        if self.k_tgs is None:
            raise AsRequestError("KEY_NOT_CONFIGURED")

        client_id = require_string_field(msg, "clientId")
        plain = self.decrypt_sensitive_payload(msg)
        username = self.validate_username(require_string_field(plain, "username"))
        password = require_string_field(plain, "password")
        nonce = require_string_field(plain, "nonce")

        issued_ms = now_ms()
        exp_ms = issued_ms + self.config.tgt_ttl_seconds * 1000
        kc_tgs = generate_des_key()

        with self.db.connection() as conn:
            try:
                # 登录必须锁定用户行，避免并发登录时 login_gen 递增结果混乱。
                user = self.db.find_user(conn, username, for_update=True)
                if user is None:
                    self.record_event(
                        conn,
                        websocket,
                        user_id=None,
                        username=username,
                        event_type="LOGIN_FAIL",
                        result=False,
                        client_id=client_id,
                        reason="BAD_CREDENTIALS",
                    )
                    conn.commit()
                    raise AsRequestError("BAD_CREDENTIALS")

                user_id = int(user["user_id"])
                login_gen_before = int(user["login_gen"])
                if int(user["status"]) != 1:
                    self.record_event(
                        conn,
                        websocket,
                        user_id=user_id,
                        username=username,
                        event_type="LOGIN_FAIL",
                        result=False,
                        client_id=client_id,
                        reason="ACCOUNT_DISABLED",
                    )
                    conn.commit()
                    raise AsRequestError("ACCOUNT_DISABLED")

                if not verify_password_hash(
                    password,
                    _bytes(user["password_salt"]),
                    int(user["pbkdf2_iter"]),
                    _bytes(user["password_hash"]),
                ):
                    self.record_event(
                        conn,
                        websocket,
                        user_id=user_id,
                        username=username,
                        event_type="LOGIN_FAIL",
                        result=False,
                        client_id=client_id,
                        reason="BAD_CREDENTIALS",
                    )
                    conn.commit()
                    raise AsRequestError("BAD_CREDENTIALS")

                # 成功登录后立即递增 login_gen；旧 TGT/Service Ticket/GS 会话
                # 将由后续 TGS/GS 对 loginGen 的校验自然失效。
                login_gen = self.db.increment_login_gen_for_login(
                    conn,
                    user_id=user_id,
                )

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

                self.record_event(
                    conn,
                    websocket,
                    user_id=user_id,
                    username=username,
                    event_type="LOGIN_SUCCESS",
                    result=True,
                    client_id=client_id,
                )
                conn.commit()
            except AsRequestError:
                raise
            except Exception:
                conn.rollback()
                raise

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

        输入报文:
        - type="CHANGE_PASSWORD_REQ"。
        - clientId: 客户端运行期实例 ID。
        - payload:
          Base64(RSA-OAEP-SHA256({"username","oldPassword","newPassword"}))。

        输出报文:
        - CHANGE_PASSWORD_REP.payload: 普通 JSON 字符串 {"ok":true}。
        - 失败时返回 ERROR，例如 BAD_CREDENTIALS、WEAK_PASSWORD、ACCOUNT_DISABLED。

        数据库副作用:
        - 成功时更新 user_account.password_hash/password_salt/pbkdf2_iter。
        - 成功时递增 user_account.login_gen，使旧票据和旧会话失效。
        - 成功和失败都会尽量记录 CHANGE_PASSWORD 安全事件。
        """

        require_fields(msg, ("clientId", "payload"))
        client_id = require_string_field(msg, "clientId")
        plain = self.decrypt_sensitive_payload(msg)
        username = self.validate_username(require_string_field(plain, "username"))
        old_password = require_string_field(plain, "oldPassword")
        new_password = require_string_field(plain, "newPassword")

        with self.db.connection() as conn:
            try:
                user = self.db.find_user(conn, username, for_update=True)
                user_id = int(user["user_id"]) if user is not None else None

                if not validate_password_policy(new_password):
                    self.record_event(
                        conn,
                        websocket,
                        user_id=user_id,
                        username=username,
                        event_type="CHANGE_PASSWORD",
                        result=False,
                        client_id=client_id,
                        reason="WEAK_PASSWORD",
                    )
                    conn.commit()
                    raise AsRequestError("WEAK_PASSWORD")

                if user is None:
                    self.record_event(
                        conn,
                        websocket,
                        user_id=None,
                        username=username,
                        event_type="CHANGE_PASSWORD",
                        result=False,
                        client_id=client_id,
                        reason="BAD_CREDENTIALS",
                    )
                    conn.commit()
                    raise AsRequestError("BAD_CREDENTIALS")

                if int(user["status"]) != 1:
                    self.record_event(
                        conn,
                        websocket,
                        user_id=user_id,
                        username=username,
                        event_type="CHANGE_PASSWORD",
                        result=False,
                        client_id=client_id,
                        reason="ACCOUNT_DISABLED",
                    )
                    conn.commit()
                    raise AsRequestError("ACCOUNT_DISABLED")

                if not verify_password_hash(
                    old_password,
                    _bytes(user["password_salt"]),
                    int(user["pbkdf2_iter"]),
                    _bytes(user["password_hash"]),
                ):
                    self.record_event(
                        conn,
                        websocket,
                        user_id=user_id,
                        username=username,
                        event_type="CHANGE_PASSWORD",
                        result=False,
                        client_id=client_id,
                        reason="BAD_CREDENTIALS",
                    )
                    conn.commit()
                    raise AsRequestError("BAD_CREDENTIALS")

                salt = generate_salt()
                password_hash = derive_password_material(
                    new_password,
                    salt,
                    self.config.pbkdf2_iter,
                )
                self.db.update_password_and_increment_login_gen(
                    conn,
                    user_id=user_id,
                    password_hash=password_hash,
                    password_salt=salt,
                    pbkdf2_iter=self.config.pbkdf2_iter,
                )
                self.record_event(
                    conn,
                    websocket,
                    user_id=user_id,
                    username=username,
                    event_type="CHANGE_PASSWORD",
                    result=True,
                    client_id=client_id,
                )
                conn.commit()
            except AsRequestError:
                raise
            except Exception:
                conn.rollback()
                raise

        return make_message(
            TYPE_CHANGE_PASSWORD_REP,
            payload=make_payload({"ok": True}),
        )


def main() -> None:
    """命令行入口。

    输入:
    - 环境变量配置。

    输出:
    - 成功时阻塞运行 AS WebSocket 服务。
    - 配置、数据库、密码学初始化失败时输出错误并以非 0 退出。
    """

    try:
        server = AsServer()
        asyncio.run(server.run())
    except (ConfigError, DatabaseError, CryptoError) as exc:
        print(f"AS startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("AS server stopped")


if __name__ == "__main__":
    main()
