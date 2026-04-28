"""AS 服务配置读取模块。

本文件只负责从环境变量读取运行配置，不连接数据库，也不启动网络服务。

主要输入：
- AUTH_DB_*：MySQL 连接参数。
- AUTH_MASTER_KEY：Fernet 主密钥，用于解开 service_key 表里的密钥密文。
- AS_* / TGS_*：AS 与 TGS 的监听地址、端口和服务名。

主要输出：
- DbConfig：数据库连接配置。
- AsConfig：AS 认证服务器运行配置。

团队约定：
- 配置缺失或格式错误时抛 ConfigError，让启动入口明确失败。
- 本模块不保存任何敏感信息到文件，只读取当前进程环境变量。
"""

from dataclasses import dataclass
import os


class ConfigError(RuntimeError):
    """配置错误。

    典型场景：
    - 必填环境变量缺失，例如 AUTH_MASTER_KEY。
    - 端口、迭代次数等整数环境变量无法解析。

    输出方式：
    - 由 as_server.py 捕获后打印启动失败原因。
    """

    pass


@dataclass(frozen=True)
class DbConfig:
    """MySQL 连接配置。

    字段说明：
    - host / port：数据库地址。
    - user / password：AS 使用的数据库账号。
    - database：认证库名称，默认 AuthDB。
    - charset：字符集，固定 utf8mb4 以支持中文用户名快照和审计信息。

    该对象只承载配置，不负责创建连接。
    """

    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"


@dataclass(frozen=True)
class AsConfig:
    """AS 认证服务运行配置。

    字段说明：
    - host / port：AS WebSocket 监听地址。
    - realm：认证域，例如 GAME.LOCAL。
    - as_service_name：AS 自己在 service_registry 中的服务名。
    - tgs_service_name：TGS 在 service_registry 中的服务名，TGT 会绑定到它。
    - tgt_ttl_seconds：TGT 默认有效期，单位秒。
    - pbkdf2_iter：注册和改密时使用的 PBKDF2 迭代次数。
    - auth_master_key：Fernet 主密钥，用于解密 service_key.key_ciphertext。
    - as_key_version / tgs_key_version：当前启用的密钥版本号。
    - tgs_host / tgs_port：种子脚本写入 TGS 服务注册信息时使用。
    """

    host: str
    port: int
    realm: str
    as_service_name: str
    tgs_service_name: str
    tgt_ttl_seconds: int
    pbkdf2_iter: int
    auth_master_key: str
    as_key_version: str
    tgs_key_version: str
    tgs_host: str
    tgs_port: int


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量。

    输入：
    - name：环境变量名。
    - default：变量不存在或为空时使用的默认值。

    输出：
    - int：解析后的整数。

    异常：
    - ConfigError：变量存在但不是合法整数。
    """

    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _required_env(name: str) -> str:
    """读取必填环境变量。

    输入：
    - name：环境变量名。

    输出：
    - str：去掉首尾空白后的变量值。

    异常：
    - ConfigError：变量不存在或为空。
    """

    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"{name} is required")
    return value.strip()


def load_db_config() -> DbConfig:
    """加载 MySQL 连接配置。

    输入：
    - 当前进程环境变量 AUTH_DB_HOST、AUTH_DB_PORT、AUTH_DB_USER、
      AUTH_DB_PASSWORD、AUTH_DB_NAME。

    输出：
    - DbConfig：供 db.AuthDb 创建连接使用。

    默认值：
    - host=127.0.0.1
    - port=3306
    - user=as_rw
    - database=AuthDB
    """

    return DbConfig(
        host=os.getenv("AUTH_DB_HOST", "127.0.0.1").strip(),
        port=_env_int("AUTH_DB_PORT", 3306),
        user=os.getenv("AUTH_DB_USER", "as_rw").strip(),
        password=os.getenv("AUTH_DB_PASSWORD", ""),
        database=os.getenv("AUTH_DB_NAME", "AuthDB").strip(),
    )


def load_as_config(require_master_key: bool = True) -> AsConfig:
    """加载 AS 服务配置。

    输入：
    - require_master_key：是否强制要求 AUTH_MASTER_KEY 存在。
      as_server.py 和 seed_auth_keys.py 必须为 True；仅生成主密钥时可为 False。

    输出：
    - AsConfig：AS 启动、签发 TGT、加载密钥时使用的完整配置。

    异常：
    - ConfigError：必填项缺失或整数项解析失败。
    """

    realm = os.getenv("AUTH_REALM", "GAME.LOCAL").strip()
    master_key = (
        _required_env("AUTH_MASTER_KEY")
        if require_master_key
        else os.getenv("AUTH_MASTER_KEY", "").strip()
    )

    return AsConfig(
        host=os.getenv("AS_HOST", "0.0.0.0").strip(),
        port=_env_int("AS_PORT", 9000),
        realm=realm,
        as_service_name=os.getenv("AUTH_AS_SERVICE_NAME", f"as/{realm}").strip(),
        tgs_service_name=os.getenv("AUTH_TGS_SERVICE_NAME", f"krbtgt/{realm}").strip(),
        tgt_ttl_seconds=_env_int("AUTH_TGT_TTL_SECONDS", 7200),
        pbkdf2_iter=_env_int("AUTH_PBKDF2_ITER", 100000),
        auth_master_key=master_key,
        as_key_version=os.getenv("AUTH_AS_KEY_VERSION", "v1").strip(),
        tgs_key_version=os.getenv("AUTH_TGS_KEY_VERSION", "v1").strip(),
        tgs_host=os.getenv("TGS_HOST", "127.0.0.1").strip(),
        tgs_port=_env_int("TGS_PORT", 9001),
    )
