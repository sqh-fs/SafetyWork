"""AS 认证协议的 JSON 报文工具。

本文件只处理“外层协议壳”，不做加密、不访问数据库。

统一报文约定：
- WebSocket 文本帧内容是 UTF-8 JSON。
- 顶层必须包含 type。
- payload 在协议层始终是字符串：
  - 认证请求中 payload 是 Base64(RSA密文)。
  - 认证响应中 payload 是 JSON 字符串。
- ERROR 报文格式固定为 {"type":"ERROR","error":"错误码"}。

主要输入：
- 客户端发来的原始 JSON 文本。
- 服务端处理完成后准备返回的字段。

主要输出：
- Python dict：供业务层读取。
- JSON 字符串：供 WebSocket 发送。
"""

import json
from typing import Any, Dict, Iterable


TYPE_REGISTER_REQ = "REGISTER_REQ"
TYPE_REGISTER_REP = "REGISTER_REP"
TYPE_AS_REQ = "AS_REQ"
TYPE_AS_REP = "AS_REP"
TYPE_CHANGE_PASSWORD_REQ = "CHANGE_PASSWORD_REQ"
TYPE_CHANGE_PASSWORD_REP = "CHANGE_PASSWORD_REP"
TYPE_ERROR = "ERROR"

SUPPORTED_AS_TYPES = {
    TYPE_REGISTER_REQ,
    TYPE_AS_REQ,
    TYPE_CHANGE_PASSWORD_REQ,
}


class ProtocolError(ValueError):
    """协议层错误。

    输入：
    - error_code：机器可读错误码，例如 INVALID_JSON、MISSING_FIELD。

    输出：
    - 业务层捕获后会转换成 ERROR 报文。
    """

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def dumps_json(obj: Any) -> str:
    """把 Python 对象编码成紧凑 JSON 字符串。

    输入：
    - obj：可 JSON 序列化的对象。

    输出：
    - str：不额外插入空格的 JSON 文本。

    说明：
    - ensure_ascii=False 保留中文，便于日志和调试阅读。
    - separators 去掉多余空格，减少网络传输体积。
    """

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads_json(raw: str) -> Dict[str, Any]:
    """解析客户端发来的顶层 JSON 报文。

    输入：
    - raw：WebSocket 收到的文本帧。

    输出：
    - dict：顶层报文对象。

    异常：
    - INVALID_JSON：文本不是合法 JSON。
    - INVALID_MESSAGE：JSON 顶层不是对象，例如传了数组或字符串。
    """

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("INVALID_JSON") from exc

    if not isinstance(data, dict):
        raise ProtocolError("INVALID_MESSAGE")

    return data


def make_message(msg_type: str, **fields: Any) -> str:
    """构造普通成功响应报文。

    输入：
    - msg_type：响应类型，例如 REGISTER_REP、AS_REP。
    - fields：除 type 外的顶层字段，例如 ticket、payload。

    输出：
    - str：可直接通过 WebSocket 发送的 JSON 字符串。

    约定：
    - 值为 None 的字段会被省略，避免协议里出现无意义 null。
    """

    msg = {"type": msg_type}
    for key, value in fields.items():
        if value is not None:
            msg[key] = value
    return dumps_json(msg)


def make_payload(obj: Dict[str, Any]) -> str:
    """把 payload 对象编码成协议要求的 JSON 字符串。

    输入：
    - obj：payload 内部对象。

    输出：
    - str：放入顶层 payload 字段的 JSON 字符串。
    """

    return dumps_json(obj)


def make_error(error_code: str, **fields: Any) -> str:
    """构造统一 ERROR 报文。

    输入：
    - error_code：机器可读错误码，客户端负责映射成界面提示。
    - fields：可选上下文字段，例如 sessionId、roomId。

    输出：
    - str：{"type":"ERROR","error":"..."} 形式的 JSON 字符串。
    """

    msg = {"type": TYPE_ERROR, "error": error_code}
    for key, value in fields.items():
        if value is not None:
            msg[key] = value
    return dumps_json(msg)


def require_fields(msg: Dict[str, Any], fields: Iterable[str]) -> None:
    """校验顶层必需字段是否存在。

    输入：
    - msg：顶层报文对象。
    - fields：该报文类型要求的字段名列表。

    输出：
    - None：全部存在时直接返回。

    异常：
    - MISSING_FIELD：字段不存在、为 None 或空字符串。
    """

    for field in fields:
        if field not in msg or msg[field] in (None, ""):
            raise ProtocolError("MISSING_FIELD")


def require_string_field(obj: Dict[str, Any], field: str) -> str:
    """读取必需字符串字段。

    输入：
    - obj：报文或 payload 对象。
    - field：字段名。

    输出：
    - str：去掉首尾空白后的字段值。

    异常：
    - MISSING_FIELD：字段不是非空字符串。
    """

    value = obj.get(field)
    if not isinstance(value, str) or value.strip() == "":
        raise ProtocolError("MISSING_FIELD")
    return value.strip()
