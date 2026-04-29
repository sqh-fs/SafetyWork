"""AS 认证服务器使用的密码学工具函数。

本文件集中实现协议中用到的基础密码学操作:
- RSA-OAEP-SHA256: 客户端用 AS 公钥加密敏感 payload，AS 用私钥解密。
- DES-CBC-PKCS7: AS 用 K_TGS 加密 TGT，用 Kuser 加密 AS_REP.payload.part。
- PBKDF2-HMAC-SHA256: 从用户密码和 salt 派生密码摘要，同时取前 8 字节作为 Kuser。
- 随机数生成: 生成 salt、KcTgs、K_TGS 和 nonce。

两表化后，长期密钥由 as_server.py 从 AS_RSA_PRIVATE_PEM /
AS_RSA_PRIVATE_KEY_PATH 和 K_TGS_BASE64 加载。
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Any, Dict, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

try:
    from Crypto.Cipher import DES
    from Crypto.Util.Padding import pad, unpad
except ImportError as exc:  # pragma: no cover - exercised only when deps are missing.
    DES = None
    pad = None
    unpad = None
    _DES_IMPORT_ERROR = exc
else:
    _DES_IMPORT_ERROR = None


DES_KEY_BYTES = 8
DES_BLOCK_BYTES = 8
PASSWORD_HASH_BYTES = 32
PASSWORD_SALT_BYTES = 16
PASSWORD_MIN_LENGTH = 8


class CryptoError(RuntimeError):
    """密码学处理错误。

    典型场景:
    - Base64 输入非法。
    - RSA 解密失败。
    - DES 密钥长度不是 8 字节。
    - DES padding 或密文格式非法。

    输出:
    - as_server.py 会把错误码转换为 ERROR 报文。
    """

    pass


def b64encode(raw: bytes) -> str:
    """把字节串编码为 Base64 文本。

    参数:
    - raw: 原始字节。

    返回:
    - ASCII Base64 字符串，适合放进 JSON 字段。
    """

    return base64.b64encode(raw).decode("ascii")


def b64decode(value: str) -> bytes:
    """把 Base64 文本解码为字节串。

    参数:
    - value: Base64 字符串。

    返回:
    - 解码后的 bytes。

    异常:
    - CryptoError("INVALID_BASE64"): 输入不是合法 Base64。
    """

    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise CryptoError("INVALID_BASE64") from exc


def generate_nonce() -> str:
    """生成协议 nonce。

    返回:
    - URL 安全的随机字符串。

    用途:
    - 客户端可在 AS_REQ 中携带 nonce，AS 在 part 中原样返回，用于确认响应
      对应本次请求。
    """

    return secrets.token_urlsafe(18)


def generate_des_key() -> bytes:
    """生成 8 字节 DES key。

    返回:
    - bytes，长度固定为 8。

    用途:
    - 生成 KcTgs。
    - seed_auth_keys.py 生成 K_TGS。
    """

    return os.urandom(DES_KEY_BYTES)


def generate_salt() -> bytes:
    """生成 PBKDF2 salt。

    返回:
    - bytes，默认 16 字节，写入 user_account.password_salt。
    """

    return os.urandom(PASSWORD_SALT_BYTES)


def validate_password_policy(password: str) -> bool:
    """校验密码强度策略。

    参数:
    - password: 客户端提交的明文密码，只在内存中短暂存在。

    返回:
    - True: 至少 8 位，且包含大写字母、小写字母和数字。
    - False: 不满足策略。
    """

    if len(password) < PASSWORD_MIN_LENGTH:
        return False
    if re.search(r"[A-Z]", password) is None:
        return False
    if re.search(r"[a-z]", password) is None:
        return False
    if re.search(r"\d", password) is None:
        return False
    return True


def normalize_username(username: str) -> str:
    """规范化用户名。

    参数:
    - username: 用户输入的用户名。

    返回:
    - trim + lower 后的用户名。

    作用:
    - 确保 LinHai、linhai、LINHAI 被视为同一个账号。
    """

    return username.strip().lower()


def derive_password_material(password: str, salt: bytes, iterations: int) -> bytes:
    """使用 PBKDF2-HMAC-SHA256 派生密码摘要。

    参数:
    - password: 明文密码。
    - salt: user_account.password_salt。
    - iterations: user_account.pbkdf2_iter。

    返回:
    - 32 字节派生结果。

    用途:
    - 完整 32 字节写入 user_account.password_hash。
    - 前 8 字节作为 Kuser，用于加密 AS_REP.payload.part。
    """

    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=PASSWORD_HASH_BYTES,
    )


def derive_kuser(password: str, salt: bytes, iterations: int) -> bytes:
    """从用户密码派生 Kuser。

    参数:
    - password/salt/iterations: 与 derive_password_material 相同。

    返回:
    - 8 字节 DES key。

    作用:
    - AS 用 Kuser 加密 part。
    - 客户端使用自己的密码同样派生 Kuser 后解密 part。
    """

    return derive_password_material(password, salt, iterations)[:DES_KEY_BYTES]


def verify_password_hash(
    password: str,
    salt: bytes,
    iterations: int,
    expected_hash: bytes,
) -> bool:
    """校验密码是否匹配数据库摘要。

    参数:
    - password: 客户端提交的明文密码。
    - salt / iterations: user_account 中保存的 PBKDF2 参数。
    - expected_hash: user_account.password_hash。

    返回:
    - True: 密码正确。
    - False: 密码错误。

    安全点:
    - 使用 hmac.compare_digest，避免普通字符串比较带来的时序差异。
    """

    actual = derive_password_material(password, salt, iterations)
    return hmac.compare_digest(actual, expected_hash)


def _json_bytes(obj: Dict[str, Any]) -> bytes:
    """把 JSON 对象稳定序列化成 UTF-8 字节。"""

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _json_object(raw: bytes) -> Dict[str, Any]:
    """把 UTF-8 JSON 字节解析成 dict。

    异常:
    - CryptoError("INVALID_JSON_PLAINTEXT"): 解密结果不是合法 JSON 对象。
    """

    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise CryptoError("INVALID_JSON_PLAINTEXT") from exc

    if not isinstance(obj, dict):
        raise CryptoError("INVALID_JSON_PLAINTEXT")
    return obj


def _require_des() -> None:
    """确认 pycryptodome 的 DES 实现可用。"""

    if DES is None:
        raise CryptoError(
            "pycryptodome is required for DES-CBC support; install as/requirements.txt"
        ) from _DES_IMPORT_ERROR


def des_encrypt_object(key: bytes, obj: Dict[str, Any]) -> str:
    """用 DES-CBC-PKCS7 加密 JSON 对象。

    参数:
    - key: 8 字节 DES key，例如 K_TGS、Kuser 或 KcTgs。
    - obj: 要加密的 JSON 对象。

    返回:
    - Base64(iv + ciphertext)。

    安全点:
    - 每次加密都随机生成 8 字节 IV。
    - 明文先做 PKCS7 padding 再进入 DES-CBC。
    """

    _require_des()
    if len(key) != DES_KEY_BYTES:
        raise CryptoError("INVALID_DES_KEY_LENGTH")

    iv = os.urandom(DES_BLOCK_BYTES)
    cipher = DES.new(key, DES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(_json_bytes(obj), DES_BLOCK_BYTES))
    return b64encode(iv + ciphertext)


def des_decrypt_object(key: bytes, ciphertext_b64: str) -> Dict[str, Any]:
    """解密 DES-CBC-PKCS7 加密的 JSON 对象。

    参数:
    - key: 8 字节 DES key。
    - ciphertext_b64: Base64(iv + ciphertext)。

    返回:
    - 解密后的 JSON dict。

    异常:
    - INVALID_DES_KEY_LENGTH: key 不是 8 字节。
    - INVALID_DES_CIPHERTEXT: 密文太短。
    - INVALID_DES_PADDING: padding 校验失败。
    """

    _require_des()
    if len(key) != DES_KEY_BYTES:
        raise CryptoError("INVALID_DES_KEY_LENGTH")

    raw = b64decode(ciphertext_b64)
    if len(raw) <= DES_BLOCK_BYTES:
        raise CryptoError("INVALID_DES_CIPHERTEXT")

    iv = raw[:DES_BLOCK_BYTES]
    ciphertext = raw[DES_BLOCK_BYTES:]
    cipher = DES.new(key, DES.MODE_CBC, iv)
    try:
        plaintext = unpad(cipher.decrypt(ciphertext), DES_BLOCK_BYTES)
    except ValueError as exc:
        raise CryptoError("INVALID_DES_PADDING") from exc
    return _json_object(plaintext)


def generate_rsa_key_pair() -> Tuple[bytes, bytes]:
    """生成 AS RSA 密钥对。

    返回:
    - (private_pem, public_pem)，均为 PEM 字节串。

    用途:
    - seed_auth_keys.py 生成本地 AS 私钥和客户端可用的 AS 公钥。
    """

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def rsa_encrypt_object(public_pem: bytes, obj: Dict[str, Any]) -> str:
    """用 AS RSA 公钥加密 JSON 对象。

    参数:
    - public_pem: AS RSA 公钥 PEM。
    - obj: 要发送给 AS 的敏感 payload。

    返回:
    - Base64(RSA-OAEP-SHA256(ciphertext))。

    用途:
    - 客户端或 smoke_test_as.py 生成 REGISTER_REQ / AS_REQ /
      CHANGE_PASSWORD_REQ 的 payload。
    """

    try:
        public_key = serialization.load_pem_public_key(public_pem)
        ciphertext = public_key.encrypt(
            _json_bytes(obj),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:
        raise CryptoError("RSA_ENCRYPT_FAILED") from exc
    return b64encode(ciphertext)


def rsa_decrypt_object(private_pem: bytes, ciphertext_b64: str) -> Dict[str, Any]:
    """用 AS RSA 私钥解密客户端 payload。

    参数:
    - private_pem: AS RSA 私钥 PEM。
    - ciphertext_b64: Base64(RSA-OAEP-SHA256(ciphertext))。

    返回:
    - 解密后的 JSON dict。

    异常:
    - RSA_DECRYPT_FAILED: 私钥格式错误、密文不匹配或 OAEP 校验失败。
    """

    try:
        private_key = serialization.load_pem_private_key(private_pem, password=None)
        plaintext = private_key.decrypt(
            b64decode(ciphertext_b64),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except CryptoError:
        raise
    except Exception as exc:
        raise CryptoError("RSA_DECRYPT_FAILED") from exc
    return _json_object(plaintext)
