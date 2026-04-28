"""AS 认证链路使用的加密与编码工具。

本文件集中实现安全相关的基础操作，避免业务代码里散落加密细节。

协议中的主要加密关系：
- REGISTER_REQ / AS_REQ / CHANGE_PASSWORD_REQ：
  客户端使用 AS 公钥做 RSA-OAEP-SHA256 加密，AS 用私钥解密。
- TGT：
  AS 使用 K_TGS 做 DES-CBC-PKCS7 加密，客户端只保存和转发，TGS 才能解开。
- AS_REP.payload.part：
  AS 使用 Kuser 做 DES-CBC-PKCS7 加密，客户端用密码派生出的 Kuser 解开。
- service_key.key_ciphertext：
  种子脚本用 AUTH_MASTER_KEY 的 Fernet 加密长期密钥，AS 启动时解开。

注意：
- DES 只用于课程 Kerberos 设计要求，真实生产环境不建议使用 DES。
- 本模块不读写数据库，也不处理 WebSocket。
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Any, Dict, Tuple

from cryptography.fernet import Fernet
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
    """加密或编码失败。

    输出方式：
    - as_server.py 会把该错误转换成 ERROR 报文。

    常见原因：
    - Base64 格式错误。
    - RSA / DES 解密失败。
    - DES key 长度不是 8 字节。
    - AUTH_MASTER_KEY 不能解开数据库中的密钥密文。
    """

    pass


def b64encode(raw: bytes) -> str:
    """把二进制数据编码成 Base64 字符串。

    输入：
    - raw：原始字节。

    输出：
    - str：ASCII Base64 文本，适合放进 JSON 字段。
    """

    return base64.b64encode(raw).decode("ascii")


def b64decode(value: str) -> bytes:
    """把 Base64 字符串解码成字节。

    输入：
    - value：Base64 文本。

    输出：
    - bytes：解码后的原始字节。

    异常：
    - CryptoError("INVALID_BASE64")：输入不是合法 Base64。
    """

    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise CryptoError("INVALID_BASE64") from exc


def generate_nonce() -> str:
    """生成认证器使用的随机串。

    输出：
    - str：URL 安全随机字符串。

    当前 AS 实现暂不主动生成 nonce，但测试或后续 TGS/GS 可复用。
    """

    return secrets.token_urlsafe(18)


def generate_des_key() -> bytes:
    """生成 8 字节 DES 会话密钥。

    输出：
    - bytes：长度固定为 8 的随机 key。

    用途：
    - AS 登录成功后生成 KcTgs。
    - 种子脚本生成长期 K_TGS。
    """

    return os.urandom(DES_KEY_BYTES)


def generate_salt() -> bytes:
    """生成 PBKDF2 密码盐。

    输出：
    - bytes：长度固定为 16 的随机 salt。
    """

    return os.urandom(PASSWORD_SALT_BYTES)


def validate_password_policy(password: str) -> bool:
    """校验注册和改密的密码复杂度。

    输入：
    - password：用户提交的明文密码。调用前已通过 RSA 加密保护传输。

    输出：
    - True：至少 8 位，包含大写字母、小写字母和数字。
    - False：不满足规则。
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

    输入：
    - username：用户输入的登录名。

    输出：
    - str：去掉首尾空白并转小写后的用户名。

    作用：
    - 保证 LinHai、linhai、 LINHAI 不会注册成多个账号。
    """

    return username.strip().lower()


def derive_password_material(password: str, salt: bytes, iterations: int) -> bytes:
    """用 PBKDF2-HMAC-SHA256 派生密码材料。

    输入：
    - password：用户明文密码。
    - salt：用户专属随机盐，来自 user_account.password_salt。
    - iterations：PBKDF2 迭代次数，来自配置或数据库。

    输出：
    - bytes：32 字节派生结果。

    用途：
    - 完整 32 字节写入 password_hash，用于登录校验。
    - 前 8 字节作为 Kuser，用于解开 AS_REP.payload.part。
    """

    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=PASSWORD_HASH_BYTES,
    )


def derive_kuser(password: str, salt: bytes, iterations: int) -> bytes:
    """派生客户端和 AS 共享的 Kuser。

    输入：
    - password、salt、iterations：与 derive_password_material 相同。

    输出：
    - bytes：派生结果前 8 字节，作为 DES key。

    作用：
    - AS 用 Kuser 加密 AS_REP.payload.part。
    - 客户端用相同规则从密码派生 Kuser 后解密 part。
    """

    return derive_password_material(password, salt, iterations)[:DES_KEY_BYTES]


def verify_password_hash(
    password: str,
    salt: bytes,
    iterations: int,
    expected_hash: bytes,
) -> bool:
    """校验用户提交的密码是否匹配数据库摘要。

    输入：
    - password：用户提交的明文密码。
    - salt / iterations：数据库保存的 PBKDF2 参数。
    - expected_hash：数据库保存的 password_hash。

    输出：
    - bool：True 表示密码正确。

    安全点：
    - 使用 hmac.compare_digest 做常量时间比较，降低计时侧信道风险。
    """

    actual = derive_password_material(password, salt, iterations)
    return hmac.compare_digest(actual, expected_hash)


def _json_bytes(obj: Dict[str, Any]) -> bytes:
    """把要加密的 JSON 对象编码成 UTF-8 字节。"""

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _json_object(raw: bytes) -> Dict[str, Any]:
    """把解密后的 UTF-8 JSON 字节解析成对象。"""

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
    """使用 DES-CBC-PKCS7 加密 JSON 对象。

    输入：
    - key：8 字节 DES key，例如 K_TGS、Kuser、KcTgs。
    - obj：要保护的明文 JSON 对象。

    输出：
    - str：Base64(iv + ciphertext)。

    协议约定：
    - IV 每次随机生成，长度 8 字节。
    - 明文使用 PKCS7 padding 补齐 DES 块大小。
    """

    _require_des()
    if len(key) != DES_KEY_BYTES:
        raise CryptoError("INVALID_DES_KEY_LENGTH")

    iv = os.urandom(DES_BLOCK_BYTES)
    cipher = DES.new(key, DES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(_json_bytes(obj), DES_BLOCK_BYTES))
    return b64encode(iv + ciphertext)


def des_decrypt_object(key: bytes, ciphertext_b64: str) -> Dict[str, Any]:
    """解密 DES-CBC-PKCS7 保护的 JSON 对象。

    输入：
    - key：8 字节 DES key。
    - ciphertext_b64：Base64(iv + ciphertext)。

    输出：
    - dict：解密并解析后的明文 JSON 对象。

    异常：
    - INVALID_DES_KEY_LENGTH：key 长度不是 8 字节。
    - INVALID_DES_CIPHERTEXT / INVALID_DES_PADDING：密文格式或 padding 错误。
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
    """生成 AS 使用的 RSA 密钥对。

    输出：
    - (private_pem, public_pem)：PEM 格式字节串。

    用途：
    - seed_auth_keys.py 写入数据库 service_key。
    - public_pem 同时导出为 as_public_key.pem 给客户端加密请求。
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
    """使用 AS 公钥加密 JSON 对象。

    输入：
    - public_pem：AS RSA 公钥 PEM。
    - obj：要加密的请求材料，例如 username/password/nonce。

    输出：
    - str：Base64(RSA-OAEP-SHA256密文)。

    典型调用方：
    - Unity 客户端。
    - smoke_test_as.py。
    """

    public_key = serialization.load_pem_public_key(public_pem)
    ciphertext = public_key.encrypt(
        _json_bytes(obj),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return b64encode(ciphertext)


def rsa_decrypt_object(private_pem: bytes, ciphertext_b64: str) -> Dict[str, Any]:
    """使用 AS 私钥解密客户端敏感 payload。

    输入：
    - private_pem：AS RSA 私钥 PEM。
    - ciphertext_b64：Base64(RSA-OAEP-SHA256密文)。

    输出：
    - dict：解密得到的 JSON 对象。

    异常：
    - RSA_DECRYPT_FAILED：密文不是用匹配公钥生成，或密文被篡改。
    """

    private_key = serialization.load_pem_private_key(private_pem, password=None)
    try:
        plaintext = private_key.decrypt(
            b64decode(ciphertext_b64),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:
        raise CryptoError("RSA_DECRYPT_FAILED") from exc
    return _json_object(plaintext)


def encrypt_key_material(master_key: str, plaintext: bytes) -> bytes:
    """用 Fernet 主密钥加密长期服务密钥。

    输入：
    - master_key：AUTH_MASTER_KEY，Fernet Base64 key。
    - plaintext：明文密钥材料，例如 RSA PEM 或 8 字节 K_TGS。

    输出：
    - bytes：写入 service_key.key_ciphertext 的密文。
    """

    return Fernet(master_key.encode("ascii")).encrypt(plaintext)


def decrypt_key_material(master_key: str, ciphertext: bytes) -> bytes:
    """解开 service_key 表中的长期密钥密文。

    输入：
    - master_key：AUTH_MASTER_KEY。
    - ciphertext：数据库 service_key.key_ciphertext 字段。

    输出：
    - bytes：明文密钥材料。

    异常：
    - KEY_DECRYPT_FAILED：主密钥错误、密文损坏或版本不匹配。
    """

    try:
        return Fernet(master_key.encode("ascii")).decrypt(ciphertext)
    except Exception as exc:
        raise CryptoError("KEY_DECRYPT_FAILED") from exc


def generate_master_key() -> str:
    """生成新的 Fernet 主密钥。

    输出：
    - str：可直接设置为 AUTH_MASTER_KEY 的字符串。
    """

    return Fernet.generate_key().decode("ascii")


def ticket_hash(ticket: str) -> str:
    """计算票据密文的 SHA-256 摘要。

    输入：
    - ticket：Base64 票据密文字符串。

    输出：
    - str：64 位十六进制摘要。

    用途：
    - 写入 ticket_issue_log.ticket_hash，便于审计且避免保存明文票据。
    """

    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()
