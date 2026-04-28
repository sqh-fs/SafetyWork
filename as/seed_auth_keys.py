"""AS/TGS 长期密钥初始化脚本。

运行目的：
- 在 service_registry 中登记 AS 和 TGS 服务。
- 在 service_key 中写入 AS RSA 私钥、公钥和 TGS 的 DES 长期密钥 K_TGS。
- 将 AS 公钥导出到 as_public_key.pem，供客户端或 smoke_test_as.py 加密请求。

运行前置条件：
- 已执行 schema_auth.sql 创建数据库表。
- 已安装 as/requirements.txt。
- 已设置 AUTH_DB_* 和 AUTH_MASTER_KEY 环境变量。

主要输入：
- 命令行参数 --rotate、--public-key-path、--generate-master-key。
- 环境变量中的数据库连接和服务配置。

主要输出：
- MySQL 中的 service_registry / service_key 记录。
- 本地 AS 公钥 PEM 文件。

安全说明：
- 私钥和 K_TGS 不以明文写入数据库，先用 AUTH_MASTER_KEY 做 Fernet 加密。
- as_public_key.pem 是公钥，可以给客户端使用；不要导出私钥文件。
"""

import argparse
from pathlib import Path

from config import ConfigError, load_as_config, load_db_config
from crypto_utils import (
    CryptoError,
    decrypt_key_material,
    encrypt_key_material,
    generate_des_key,
    generate_master_key,
    generate_rsa_key_pair,
)
from db import AuthDb, DbError


def seed_keys(rotate: bool, public_key_path: Path) -> None:
    """生成或复用 AS/TGS 密钥并写入数据库。

    输入：
    - rotate：True 时覆盖当前版本密钥；False 时已有密钥就复用。
    - public_key_path：AS 公钥导出路径。

    输出：
    - None；执行成功后会打印服务名和公钥路径。

    数据库副作用：
    - upsert service_registry：AS、TGS 服务记录。
    - upsert service_key：AS_RSA_PRIVATE、AS_RSA_PUBLIC、K_TGS。

    文件副作用：
    - 写入 public_key_path，内容为 PEM 格式 RSA 公钥。
    """

    db = AuthDb(load_db_config())
    config = load_as_config(require_master_key=True)

    with db.connection() as conn:
        # service_registry 记录服务如何被客户端或其他服务识别。
        as_url = f"ws://{config.host}:{config.port}"
        tgs_url = f"ws://{config.tgs_host}:{config.tgs_port}"

        as_service_id = db.upsert_service(
            conn,
            service_name=config.as_service_name,
            service_type="AS",
            realm=config.realm,
            host=config.host,
            port=config.port,
            websocket_url=as_url,
        )
        tgs_service_id = db.upsert_service(
            conn,
            service_name=config.tgs_service_name,
            service_type="TGS",
            realm=config.realm,
            host=config.tgs_host,
            port=config.tgs_port,
            websocket_url=tgs_url,
        )

        existing_private = db.get_service_key(
            conn,
            config.as_service_name,
            "AS_RSA_PRIVATE",
            config.as_key_version,
        )
        existing_public = db.get_service_key(
            conn,
            config.as_service_name,
            "AS_RSA_PUBLIC",
            config.as_key_version,
        )
        # rotate=False 时尽量复用已有密钥，避免客户端公钥突然失效。
        if rotate or existing_private is None or existing_public is None:
            private_pem, public_pem = generate_rsa_key_pair()
            db.upsert_service_key(
                conn,
                as_service_id,
                key_usage="AS_RSA_PRIVATE",
                key_version=config.as_key_version,
                algorithm="RSA",
                key_ciphertext=encrypt_key_material(config.auth_master_key, private_pem),
            )
            db.upsert_service_key(
                conn,
                as_service_id,
                key_usage="AS_RSA_PUBLIC",
                key_version=config.as_key_version,
                algorithm="RSA",
                key_ciphertext=encrypt_key_material(config.auth_master_key, public_pem),
            )
        else:
            public_pem = decrypt_key_material(
                config.auth_master_key,
                bytes(existing_public["key_ciphertext"]),
            )

        existing_tgs = db.get_service_key(
            conn,
            config.tgs_service_name,
            "K_TGS",
            config.tgs_key_version,
        )
        # K_TGS 是 AS 签发 TGT、TGS 解密 TGT 的长期共享密钥。
        if rotate or existing_tgs is None:
            db.upsert_service_key(
                conn,
                tgs_service_id,
                key_usage="K_TGS",
                key_version=config.tgs_key_version,
                algorithm="DES",
                key_ciphertext=encrypt_key_material(
                    config.auth_master_key,
                    generate_des_key(),
                ),
            )

        conn.commit()

    public_key_path.parent.mkdir(parents=True, exist_ok=True)
    public_key_path.write_bytes(public_pem)
    print(f"AS public key exported: {public_key_path}")
    print(f"AS service: {config.as_service_name}")
    print(f"TGS service: {config.tgs_service_name}")


def main() -> None:
    """命令行入口。

    输入：
    - --generate-master-key：只生成 Fernet 主密钥并退出，不访问数据库。
    - --rotate：强制替换当前版本密钥。
    - --public-key-path：指定 AS 公钥导出路径。

    输出：
    - 成功时写库和导出公钥；失败时返回退出码 2。
    """

    parser = argparse.ArgumentParser(description="Seed AS/TGS keys into AuthDB.")
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Overwrite existing configured key version values.",
    )
    parser.add_argument(
        "--public-key-path",
        default=str(Path(__file__).with_name("as_public_key.pem")),
        help="Where to export the plaintext AS public key PEM.",
    )
    parser.add_argument(
        "--generate-master-key",
        action="store_true",
        help="Print a fresh Fernet master key and exit.",
    )
    args = parser.parse_args()

    if args.generate_master_key:
        print(generate_master_key())
        return

    try:
        seed_keys(args.rotate, Path(args.public_key_path))
    except (ConfigError, CryptoError, DbError) as exc:
        print(f"seed failed: {exc}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
