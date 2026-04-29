"""生成 AS 本地密钥材料的脚本。

两表化后，AS 长期密钥只从本地文件或环境变量加载。本脚本只在本地生成:
- AS RSA 私钥 PEM: 供 as_server.py 通过 AS_RSA_PRIVATE_KEY_PATH 加载。
- AS RSA 公钥 PEM: 供客户端或 smoke_test_as.py 离线加密 payload。
- K_TGS Base64 文本: 供 as_server.py 通过 K_TGS_BASE64 加载。

运行前置条件:
- 已安装 as/requirements.txt 中的 cryptography。
- 不需要 MySQL，不需要 AUTH_DB_*。

默认输出:
- as/as_private_key.pem: 私钥，应被 .gitignore 忽略。
- as/as_public_key.pem: 公钥，可按团队需要提交或分发。
- as/k_tgs_base64.txt: K_TGS 示例值，应被 .gitignore 忽略。
"""

import argparse
from pathlib import Path

from crypto_utils import b64encode, generate_des_key, generate_rsa_key_pair


DEFAULT_PRIVATE_KEY_PATH = Path(__file__).with_name("as_private_key.pem")
DEFAULT_PUBLIC_KEY_PATH = Path(__file__).with_name("as_public_key.pem")
DEFAULT_K_TGS_PATH = Path(__file__).with_name("k_tgs_base64.txt")


def write_bytes(path: Path, data: bytes, *, overwrite: bool) -> None:
    """写入二进制文件。

    参数:
    - path: 输出文件路径。
    - data: 要写入的字节内容。
    - overwrite: False 时，如果文件已存在则拒绝覆盖。

    返回:
    - None。

    文件副作用:
    - 创建父目录。
    - 写入 PEM 私钥或公钥文件。
    """

    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; use --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_text(path: Path, data: str, *, overwrite: bool) -> None:
    """写入文本文件。

    参数:
    - path: 输出文件路径。
    - data: 要写入的文本。
    - overwrite: False 时，如果文件已存在则拒绝覆盖。

    返回:
    - None。

    文件副作用:
    - 创建父目录。
    - 写入 K_TGS Base64 文本。
    """

    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; use --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    输入:
    - --private-key-path: AS 私钥输出路径。
    - --public-key-path: AS 公钥输出路径。
    - --k-tgs-path: K_TGS Base64 输出路径。
    - --overwrite: 允许覆盖已有密钥材料。

    返回:
    - argparse.Namespace。
    """

    parser = argparse.ArgumentParser(
        description="Generate local AS RSA keys and K_TGS for the two-table AS server."
    )
    parser.add_argument(
        "--private-key-path",
        type=Path,
        default=DEFAULT_PRIVATE_KEY_PATH,
        help="AS RSA private key output path",
    )
    parser.add_argument(
        "--public-key-path",
        type=Path,
        default=DEFAULT_PUBLIC_KEY_PATH,
        help="AS RSA public key output path",
    )
    parser.add_argument(
        "--k-tgs-path",
        type=Path,
        default=DEFAULT_K_TGS_PATH,
        help="K_TGS Base64 output path",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite existing key files",
    )
    return parser.parse_args()


def main() -> None:
    """生成密钥文件并打印启动 AS 需要的环境变量。

    输出:
    - 写入私钥、公钥和 K_TGS 文本文件。
    - 在终端打印 PowerShell 环境变量设置示例。
    """

    args = parse_args()
    private_pem, public_pem = generate_rsa_key_pair()
    k_tgs_base64 = b64encode(generate_des_key())

    write_bytes(args.private_key_path, private_pem, overwrite=args.overwrite)
    write_bytes(args.public_key_path, public_pem, overwrite=args.overwrite)
    write_text(args.k_tgs_path, k_tgs_base64 + "\n", overwrite=args.overwrite)

    print("Generated AS key material:")
    print(f"  private key: {args.private_key_path}")
    print(f"  public key : {args.public_key_path}")
    print(f"  K_TGS file : {args.k_tgs_path}")
    print()
    print("PowerShell example for starting as_server.py:")
    print(f"$env:AS_RSA_PRIVATE_KEY_PATH='{args.private_key_path}'")
    print(f"$env:K_TGS_BASE64='{k_tgs_base64}'")
    print(f"$env:AS_PUBLIC_KEY_PATH='{args.public_key_path}'")


if __name__ == "__main__":
    main()
