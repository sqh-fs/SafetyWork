"""AS 认证服务器的环境变量配置读取模块。

本文件只负责把环境变量整理成结构化配置对象，不连接数据库、不创建密钥、
也不启动 WebSocket 服务。AS 两表化后，运行时长期密钥不再从 MySQL 的
数据库读取，而是从环境变量或本地 PEM 文件读取。

主要输入:
- AUTH_DB_HOST / AUTH_DB_PORT / AUTH_DB_USER / AUTH_DB_PASSWORD / AUTH_DB_NAME:
  MySQL 连接参数。
- AS_HOST / AS_PORT:
  AS WebSocket 监听地址，默认 0.0.0.0:9000。
- AS_RSA_PRIVATE_PEM / AS_RSA_PRIVATE_KEY_PATH:
  AS RSA 私钥来源，二选一即可。
- K_TGS_BASE64:
  TGS 长期 DES 密钥的 Base64 文本，解码后必须正好是 8 字节。

主要输出:
- DbConfig: 数据库连接配置。
- AsConfig: AS 协议、安全参数和密钥来源配置。
"""

import os
from dataclasses import dataclass
from typing import Optional


class ConfigError(RuntimeError):
    """配置错误。

    典型触发场景:
    - 必填环境变量缺失，例如 AUTH_DB_USER 或 AUTH_DB_NAME。
    - 数值型环境变量无法转成整数。
    - AS_HOST、AS_PORT 等启动参数不合法。
    """

    pass


@dataclass(frozen=True)
class DbConfig:
    """MySQL 连接配置。

    字段含义:
    - host / port: MySQL 服务地址。
    - user / password: 认证账号。
    - database: AS 使用的认证库名称，需先执行 schema_auth.sql 初始化。
    - charset: 连接字符集，固定使用 utf8mb4，避免用户名或审计原因出现中文时乱码。
    """

    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"


@dataclass(frozen=True)
class AsConfig:
    """AS 认证服务器运行配置。

    字段含义:
    - host / port: WebSocket 监听地址。
    - realm: 票据所属认证域，写入 TGT 明文字段后再被 K_TGS 加密。
    - tgs_service_name: TGT 绑定的逻辑服务名，默认 krbtgt/{realm}。
    - tgt_ttl_seconds: TGT 有效期秒数，默认 2 小时。
    - pbkdf2_iter: 新注册/改密时写入 user_account.pbkdf2_iter 的迭代次数。
    - as_private_key_pem: 直接来自 AS_RSA_PRIVATE_PEM 的私钥文本，可为空。
    - as_private_key_path: 来自 AS_RSA_PRIVATE_KEY_PATH 的私钥文件路径，可为空。
    - k_tgs_base64: 来自 K_TGS_BASE64 的 TGS 长期 DES 密钥文本。
    """

    host: str
    port: int
    realm: str
    tgs_service_name: str
    tgt_ttl_seconds: int
    pbkdf2_iter: int
    as_private_key_pem: Optional[str]
    as_private_key_path: Optional[str]
    k_tgs_base64: str


def _required_env(name: str) -> str:
    """读取必填环境变量。

    参数:
    - name: 环境变量名。

    返回:
    - 去除首尾空白后的字符串值。

    异常:
    - ConfigError: 变量不存在或值为空。
    """

    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"missing required environment variable: {name}")
    return value.strip()


def _optional_env(name: str) -> Optional[str]:
    """读取可选环境变量。

    参数:
    - name: 环境变量名。

    返回:
    - 未设置或全空白时返回 None，否则返回去除首尾空白后的字符串。
    """

    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _int_env(name: str, default: int) -> int:
    """读取整数环境变量。

    参数:
    - name: 环境变量名。
    - default: 未设置时使用的默认值。

    返回:
    - int 类型配置值。

    异常:
    - ConfigError: 变量已设置但不是合法整数。
    """

    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"environment variable {name} must be an integer") from exc


def load_db_config() -> DbConfig:
    """加载 MySQL 连接配置。

    输入:
    - AUTH_DB_HOST: 默认 127.0.0.1。
    - AUTH_DB_PORT: 默认 3306。
    - AUTH_DB_USER / AUTH_DB_NAME: 必填。
    - AUTH_DB_PASSWORD: 可为空字符串。

    输出:
    - DbConfig，用于 db.py 创建 PyMySQL 连接。
    """

    return DbConfig(
        host=os.getenv("AUTH_DB_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int_env("AUTH_DB_PORT", 3306),
        user=_required_env("AUTH_DB_USER"),
        password=os.getenv("AUTH_DB_PASSWORD", ""),
        database=_required_env("AUTH_DB_NAME"),
    )


def load_as_config() -> AsConfig:
    """加载 AS 协议和密钥来源配置。

    输入:
    - AS_HOST / AS_PORT: 监听配置。
    - AUTH_REALM: 认证域，默认 SAFETYWORK。
    - AUTH_TGS_SERVICE_NAME: TGS 逻辑服务名，默认 krbtgt/{realm}。
    - AUTH_TGT_TTL_SECONDS: TGT 有效期，默认 7200 秒。
    - AUTH_PBKDF2_ITER: PBKDF2 默认迭代次数，默认 100000。
    - AS_RSA_PRIVATE_PEM 或 AS_RSA_PRIVATE_KEY_PATH: RSA 私钥来源。
    - K_TGS_BASE64: TGS 长期 DES 密钥，必填。

    输出:
    - AsConfig。密钥内容的格式校验在 as_server.load_runtime_keys() 中完成。
    """

    realm = os.getenv("AUTH_REALM", "SAFETYWORK").strip() or "SAFETYWORK"
    tgs_service_name = (
        os.getenv("AUTH_TGS_SERVICE_NAME", f"krbtgt/{realm}").strip()
        or f"krbtgt/{realm}"
    )
    return AsConfig(
        host=os.getenv("AS_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=_int_env("AS_PORT", 9000),
        realm=realm,
        tgs_service_name=tgs_service_name,
        tgt_ttl_seconds=_int_env("AUTH_TGT_TTL_SECONDS", 7200),
        pbkdf2_iter=_int_env("AUTH_PBKDF2_ITER", 100000),
        as_private_key_pem=_optional_env("AS_RSA_PRIVATE_PEM"),
        as_private_key_path=_optional_env("AS_RSA_PRIVATE_KEY_PATH"),
        k_tgs_base64=_required_env("K_TGS_BASE64"),
    )
