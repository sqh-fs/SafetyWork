"""AS 端到端冒烟测试脚本。

测试目标：
- 使用 as_public_key.pem 模拟客户端加密请求。
- 通过 WebSocket 连到 AS。
- 依次验证注册、登录、loginGen 递增、改密、旧密码失效、新密码可登录。

运行前置条件：
- MySQL 已执行 schema_auth.sql。
- 已运行 seed_auth_keys.py 导出 AS 公钥。
- AS 服务已启动。
- 已安装 as/requirements.txt。

主要输入：
- AS_URL：AS WebSocket 地址，默认 ws://127.0.0.1:9000。
- AS_PUBLIC_KEY_PATH：AS 公钥路径，默认 as/as_public_key.pem。

主要输出：
- 成功时打印 "AS smoke test passed"。
- 任一断言失败时抛 AssertionError，指出协议或行为不符合预期。
"""

import asyncio
import json
import os
from pathlib import Path
import secrets

import websockets

from crypto_utils import (
    b64decode,
    derive_kuser,
    des_decrypt_object,
    rsa_encrypt_object,
)
from protocol import (
    TYPE_AS_REP,
    TYPE_AS_REQ,
    TYPE_CHANGE_PASSWORD_REP,
    TYPE_CHANGE_PASSWORD_REQ,
    TYPE_ERROR,
    TYPE_REGISTER_REP,
    TYPE_REGISTER_REQ,
    loads_json,
    make_message,
)


AS_URL = os.getenv("AS_URL", "ws://127.0.0.1:9000")
PUBLIC_KEY_PATH = Path(
    os.getenv("AS_PUBLIC_KEY_PATH", str(Path(__file__).with_name("as_public_key.pem")))
)


def load_public_key() -> bytes:
    """读取 AS 公钥 PEM。

    输入：
    - AS_PUBLIC_KEY_PATH 环境变量或默认路径。

    输出：
    - bytes：PEM 格式公钥。

    异常：
    - FileNotFoundError：未运行 seed_auth_keys.py 或路径配置错误。
    """

    if not PUBLIC_KEY_PATH.exists():
        raise FileNotFoundError(
            f"AS public key not found: {PUBLIC_KEY_PATH}. Run seed_auth_keys.py first."
        )
    return PUBLIC_KEY_PATH.read_bytes()


async def request(ws, message: str) -> dict:
    """发送一条请求并要求 AS 返回成功响应。

    输入：
    - ws：已连接的 WebSocket。
    - message：JSON 文本帧。

    输出：
    - dict：AS 返回的非 ERROR 报文。

    异常：
    - AssertionError：AS 返回 ERROR。
    """

    await ws.send(message)
    raw = await ws.recv()
    msg = loads_json(raw)
    if msg.get("type") == TYPE_ERROR:
        raise AssertionError(f"AS returned ERROR: {msg.get('error')}")
    return msg


async def request_error(ws, message: str, expected_error: str) -> dict:
    """发送一条请求并要求 AS 返回指定 ERROR。

    输入：
    - ws：已连接的 WebSocket。
    - message：JSON 文本帧。
    - expected_error：期望的错误码。

    输出：
    - dict：AS 返回的 ERROR 报文。

    用途：
    - 验证改密后旧密码登录会失败。
    """

    await ws.send(message)
    raw = await ws.recv()
    msg = loads_json(raw)
    if msg.get("type") != TYPE_ERROR:
        raise AssertionError(f"expected ERROR, got {msg}")
    if msg.get("error") != expected_error:
        raise AssertionError(f"expected {expected_error}, got {msg.get('error')}")
    return msg


def encrypted_message(public_key: bytes, msg_type: str, client_id: str, payload: dict) -> str:
    """构造客户端侧 RSA 加密请求。

    输入：
    - public_key：AS 公钥 PEM。
    - msg_type：REGISTER_REQ、AS_REQ 或 CHANGE_PASSWORD_REQ。
    - client_id：客户端实例 ID。
    - payload：请求明文对象。

    输出：
    - str：可直接发送给 AS 的顶层 JSON 报文。
    """

    return make_message(
        msg_type,
        clientId=client_id,
        payload=rsa_encrypt_object(public_key, payload),
    )


def decode_as_part(password: str, response: dict) -> dict:
    """解开 AS_REP.payload.part。

    输入：
    - password：本次登录使用的明文密码。
    - response：AS_REP 顶层对象。

    输出：
    - dict：part 明文，包含 nonce、kcTgs、exp、loginGen 等字段。

    验证点：
    - 客户端必须用 salt/iter 派生 Kuser，才能解开 part。
    """

    payload = json.loads(response["payload"])
    salt = b64decode(payload["salt"])
    iterations = int(payload["iter"])
    kuser = derive_kuser(password, salt, iterations)
    return des_decrypt_object(kuser, payload["part"])


async def login(ws, public_key: bytes, client_id: str, username: str, password: str, nonce: str) -> dict:
    """完成一次 AS_REQ 登录并校验 nonce。

    输入：
    - ws：WebSocket 连接。
    - public_key：AS 公钥。
    - client_id：客户端实例 ID。
    - username / password：测试账号口令。
    - nonce：客户端发出的随机串。

    输出：
    - dict：解密后的 AS_REP.payload.part。

    异常：
    - AssertionError：响应类型不是 AS_REP 或 nonce 不匹配。
    """

    response = await request(
        ws,
        encrypted_message(
            public_key,
            TYPE_AS_REQ,
            client_id,
            {"username": username, "password": password, "nonce": nonce},
        ),
    )
    if response.get("type") != TYPE_AS_REP:
        raise AssertionError(f"expected AS_REP, got {response}")
    part = decode_as_part(password, response)
    if part.get("nonce") != nonce:
        raise AssertionError("AS_REP nonce mismatch")
    return part


async def main() -> None:
    """执行完整 AS 冒烟测试。

    流程：
    1. 生成唯一测试用户名，避免与已有数据冲突。
    2. 注册新账号。
    3. 连续登录两次，确认 loginGen 递增。
    4. 修改密码。
    5. 验证旧密码失败，新密码成功。
    """

    public_key = load_public_key()
    suffix = secrets.token_hex(5)
    username = f"smoke_{suffix}"
    password = "SmokePass1"
    new_password = "SmokePass2"
    client_id = f"cli-smoke-{suffix}"

    async with websockets.connect(AS_URL) as ws:
        register_response = await request(
            ws,
            encrypted_message(
                public_key,
                TYPE_REGISTER_REQ,
                client_id,
                {"username": username, "password": password},
            ),
        )
        if register_response.get("type") != TYPE_REGISTER_REP:
            raise AssertionError(f"expected REGISTER_REP, got {register_response}")

        first_login = await login(ws, public_key, client_id, username, password, "n1")
        second_login = await login(ws, public_key, client_id, username, password, "n2")
        if int(second_login["loginGen"]) <= int(first_login["loginGen"]):
            raise AssertionError("loginGen did not increase on successful login")

        change_response = await request(
            ws,
            encrypted_message(
                public_key,
                TYPE_CHANGE_PASSWORD_REQ,
                client_id,
                {
                    "username": username,
                    "oldPassword": password,
                    "newPassword": new_password,
                },
            ),
        )
        if change_response.get("type") != TYPE_CHANGE_PASSWORD_REP:
            raise AssertionError(f"expected CHANGE_PASSWORD_REP, got {change_response}")

        await request_error(
            ws,
            encrypted_message(
                public_key,
                TYPE_AS_REQ,
                client_id,
                {"username": username, "password": password, "nonce": "n3"},
            ),
            "BAD_CREDENTIALS",
        )

        await login(ws, public_key, client_id, username, new_password, "n4")

    print("AS smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
