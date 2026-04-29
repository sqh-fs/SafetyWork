"""AS WebSocket 基本链路 smoke test。

本脚本通过 AS 公钥构造真实协议请求，覆盖:
- 注册新用户。
- 连续登录并确认 loginGen 递增。
- 修改密码。
- 旧密码登录失败。
- 新密码登录成功。

运行前置条件:
- 已执行 as/schema_auth.sql 初始化两张表。
- 已运行 seed_auth_keys.py 生成 as/as_public_key.pem、AS 私钥和 K_TGS。
- 已启动 as_server.py，并为服务端设置 AS_RSA_PRIVATE_KEY_PATH 和 K_TGS_BASE64。
- 已安装 as/requirements.txt。

输入环境变量:
- AS_URL: AS WebSocket 地址，默认 ws://127.0.0.1:9000。
- AS_PUBLIC_KEY_PATH: AS 公钥路径，默认 as/as_public_key.pem。

输出:
- 全部断言通过时打印 "AS smoke test passed"。
- 协议错误、断言失败或连接失败时抛出异常，便于 CI 或人工排查。
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

    输入:
    - AS_PUBLIC_KEY_PATH 指向的 PEM 文件。

    返回:
    - bytes，AS RSA 公钥 PEM。

    异常:
    - FileNotFoundError: 尚未运行 seed_auth_keys.py 或路径设置错误。
    """

    if not PUBLIC_KEY_PATH.exists():
        raise FileNotFoundError(
            f"AS public key not found: {PUBLIC_KEY_PATH}. Run seed_auth_keys.py first."
        )
    return PUBLIC_KEY_PATH.read_bytes()


async def request(ws, message: str) -> dict:
    """发送一条请求并要求 AS 返回非 ERROR 响应。

    参数:
    - ws: WebSocket 连接。
    - message: JSON 协议字符串。

    返回:
    - AS 响应 dict。

    异常:
    - AssertionError: AS 返回 ERROR。
    """

    await ws.send(message)
    raw = await ws.recv()
    msg = loads_json(raw)
    if msg.get("type") == TYPE_ERROR:
        raise AssertionError(f"AS returned ERROR: {msg.get('error')}")
    return msg


async def request_error(ws, message: str, expected_error: str) -> dict:
    """发送一条请求并要求 AS 返回指定 ERROR。

    参数:
    - ws: WebSocket 连接。
    - message: JSON 协议字符串。
    - expected_error: 期望的机器可读错误码。

    返回:
    - ERROR 响应 dict。
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
    """构造带 RSA 加密 payload 的 AS 请求。

    参数:
    - public_key: AS RSA 公钥 PEM。
    - msg_type: REGISTER_REQ、AS_REQ 或 CHANGE_PASSWORD_REQ。
    - client_id: 写入顶层 clientId，也会进入 security_event_log。
    - payload: 待 RSA 加密的敏感 JSON 对象。

    返回:
    - 可直接发送到 WebSocket 的 JSON 字符串。
    """

    return make_message(
        msg_type,
        clientId=client_id,
        payload=rsa_encrypt_object(public_key, payload),
    )


def decode_as_part(password: str, response: dict) -> dict:
    """解密 AS_REP.payload.part。

    参数:
    - password: 当前登录密码，用于按 salt/iter 派生 Kuser。
    - response: AS_REP 响应 dict。

    返回:
    - part JSON dict，包含 nonce、kcTgs、exp、loginGen 等字段。
    """

    payload = json.loads(response["payload"])
    salt = b64decode(payload["salt"])
    iterations = int(payload["iter"])
    kuser = derive_kuser(password, salt, iterations)
    return des_decrypt_object(kuser, payload["part"])


async def login(
    ws,
    public_key: bytes,
    client_id: str,
    username: str,
    password: str,
    nonce: str,
) -> dict:
    """执行一次 AS_REQ 登录并校验 nonce。

    参数:
    - ws: WebSocket 连接。
    - public_key: AS RSA 公钥 PEM。
    - client_id: 顶层 clientId。
    - username/password: 登录凭据。
    - nonce: 本次请求 nonce。

    返回:
    - 解密后的 AS_REP.payload.part。
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
    """执行完整 smoke test 流程。

    数据库副作用:
    - 新增一个随机用户名的 user_account。
    - 写入 REGISTER、LOGIN_SUCCESS、LOGIN_FAIL、CHANGE_PASSWORD 安全事件。
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
