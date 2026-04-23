import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

import game_simulation
from game_config import HOST, JUMP_VELOCITY, MAX_JUMP_COUNT, MOVE_SPEED, PORT, SIM_DT
from game_models import ClientSession, Platform


class RelayServer:
    """WebSocket 游戏中继服务，负责连接、房间和协议消息处理。"""

    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        self.host = host
        self.port = port
        self.sessions: Dict[Any, ClientSession] = {}
        self.rooms: Dict[str, Set[Any]] = {}
        self.tick: int = 0

    async def run(self) -> None:
        print("=" * 72)
        print(f"[SERVER] WebSocket 游戏服务启动: ws://{self.host}:{self.port}")
        print("[SERVER] 模式: 轻量规则状态机 + 简化地图逻辑 + 统一速度模型")
        print(f"[SERVER] SIM_DT={SIM_DT} MOVE_SPEED={MOVE_SPEED}")
        print("=" * 72)

        async with websockets.serve(self.handle_client, self.host, self.port):
            await asyncio.Future()

    async def handle_client(self, websocket: Any) -> None:
        remote = websocket.remote_address
        self.sessions[websocket] = ClientSession()
        print(f"[CONNECT] 新连接: remote={remote} | 当前连接数={len(self.sessions)}")

        try:
            async for raw_message in websocket:
                await self.handle_message(websocket, raw_message)
        except ConnectionClosed as close_info:
            print(f"[CLOSED ] remote={remote} | code={close_info.code} | reason={close_info.reason}")
        finally:
            await self.cleanup_client(websocket, reason="disconnect")

    async def handle_message(self, websocket: Any, raw_message: str) -> None:
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            await self.send_error(websocket, "无效 JSON：请发送合法的 JSON 字符串")
            return

        msg_type = str(data.get("type", "")).strip()
        if not msg_type:
            await self.send_error(websocket, "缺少字段 type")
            return

        if msg_type == "JOIN_ROOM":
            await self.handle_join_room(websocket, data)
            return
        if msg_type == "INPUT":
            await self.handle_input(websocket, data)
            return
        if msg_type == "CHAT":
            await self.handle_chat(websocket, data)
            return
        if msg_type == "LEAVE_ROOM":
            await self.handle_leave_room(websocket, data)
            return

        await self.send_error(websocket, f"未知消息类型: {msg_type}")

    async def handle_join_room(self, websocket: Any, data: Dict[str, Any]) -> None:
        client_id = str(data.get("clientId", "")).strip()
        room_id = str(data.get("roomId", "")).strip()

        if not client_id or not room_id:
            await self.send_error(websocket, "JOIN_ROOM 缺少 clientId 或 roomId")
            return

        session = self.sessions.get(websocket)
        if session is None:
            await self.send_error(websocket, "服务端未找到该连接的会话")
            return

        if session.room_id:
            self.remove_from_room(websocket, session.room_id)

        session.client_id = client_id
        session.room_id = room_id
        session.last_seq = -1
        session.accepted_state = "Grounded"
        session.accepted_grounded = True
        session.accepted_jump_count = 0
        session.accepted_drop = False
        session.vel_x = 0.0
        session.vel_y = 0.0

        if client_id.endswith("1"):
            session.pos_x = 0.0
        else:
            session.pos_x = 2.0

        session.pos_y = 3.0

        self.rooms.setdefault(room_id, set()).add(websocket)

        ack = {
            "type": "SERVER_BROADCAST",
            "roomId": room_id,
            "fromClientId": "SERVER",
            "text": f"{client_id} 已加入房间 {room_id}",
            "timestamp": self.utc_now_iso(),
        }
        await self.send_json(websocket, ack)
        await self.send_snapshot(websocket, session, "")

    async def handle_input(self, websocket: Any, data: Dict[str, Any]) -> None:
        session = self.sessions.get(websocket)
        if session is None or not session.room_id or not session.client_id:
            await self.send_error(websocket, "请先 JOIN_ROOM，再发送 INPUT")
            return

        payload_raw = data.get("payload", "")
        if not payload_raw:
            await self.send_error(websocket, "INPUT 缺少 payload")
            return

        try:
            cmd = json.loads(payload_raw)
        except json.JSONDecodeError:
            await self.send_error(websocket, "INPUT payload 不是合法 JSON")
            return

        seq = int(cmd.get("seq", 0))
        input_x = float(cmd.get("moveX", 0.0))
        input_x = max(-1.0, min(1.0, input_x))

        jump_pressed = bool(cmd.get("jumpPressed", False))
        down_held = bool(cmd.get("downHeld", False))
        drop_pressed = bool(cmd.get("dropPressed", False))

        client_state = str(cmd.get("clientState", "Unknown"))
        client_grounded = bool(cmd.get("clientGrounded", False))
        client_jump_count = int(cmd.get("clientJumpCount", 0))
        client_pos_x = float(cmd.get("clientPosX", 0.0))
        _client_vel_x = float(cmd.get("clientVelX", 0.0))

        session.last_seq = seq
        session.accepted_drop = False
        reject_reason = ""

        # 水平移动
        session.vel_x = input_x * MOVE_SPEED
        next_x = session.pos_x + session.vel_x * SIM_DT

        if not self.hits_wall(next_x, session.pos_y):
            session.pos_x = next_x
        else:
            session.vel_x = 0.0
            reject_reason = "撞墙阻挡"

        # 先按当前位置刷新 grounded（这一拍开始时是否站地）
        standing_platform = self.get_standing_platform(session)
        if standing_platform is not None and session.vel_y <= 0:
            session.accepted_grounded = True
            session.pos_y = standing_platform.y
            session.vel_y = 0.0
            if session.accepted_state not in ("Dash", "BasicAttack"):
                session.accepted_state = "Grounded"
            session.accepted_jump_count = 0
        else:
            session.accepted_grounded = False
            if session.accepted_state == "Grounded":
                session.accepted_state = client_state or "Airborne"

        # 先处理 drop-through
        current_platform = self.get_standing_platform(session)
        if drop_pressed and down_held:
            if current_platform is not None and current_platform.kind == "oneway":
                session.accepted_drop = True
                session.accepted_grounded = False
                session.accepted_state = "Fall"
                session.vel_y = min(session.vel_y, -2.0)
                session.pos_y -= 0.15
            else:
                reject_reason = "当前不在可下落的单向平台上"

        # 再处理 jump（关键：前置）
        elif jump_pressed:
            if session.accepted_grounded:
                session.accepted_grounded = False
                session.accepted_jump_count = 1
                session.accepted_state = "Jump"
                session.vel_y = JUMP_VELOCITY
            elif session.accepted_jump_count < MAX_JUMP_COUNT:
                session.accepted_jump_count += 1
                session.accepted_state = "Jump"
                session.vel_y = JUMP_VELOCITY
            else:
                reject_reason = "超过最大跳跃次数"

        # 最后才做本帧垂直推进（关键）
        self.step_vertical(session)

        # 用推进后的结果再刷新 grounded
        standing_platform = self.get_standing_platform(session)
        if standing_platform is not None and session.vel_y <= 0:
            session.accepted_grounded = True
            session.vel_y = 0.0
            session.pos_y = standing_platform.y
            if session.accepted_state not in ("Dash", "BasicAttack"):
                session.accepted_state = "Grounded"
            session.accepted_jump_count = 0
        else:
            session.accepted_grounded = False

        if not session.accepted_grounded and session.accepted_jump_count == 0 and client_jump_count > 0:
            session.accepted_jump_count = min(client_jump_count, MAX_JUMP_COUNT)

        self.tick += 1

        print(
            f"[INPUT] client={session.client_id} seq={seq} "
            f"inputX={input_x:.2f} velX={session.vel_x:.2f} "
            f"clientState={client_state} clientGrounded={client_grounded} "
            f"clientJumpCount={client_jump_count} jumpPressed={jump_pressed} "
            f"downHeld={down_held} dropPressed={drop_pressed} -> "
            f"acceptedState={session.accepted_state} acceptedGrounded={session.accepted_grounded} "
            f"acceptedJumpCount={session.accepted_jump_count} acceptedDrop={session.accepted_drop} "
            f"pos=({session.pos_x:.2f},{session.pos_y:.2f}) "
            f"vel=({session.vel_x:.2f},{session.vel_y:.2f}) reject={reject_reason} "
            f"serverPosX={session.pos_x:.3f} serverVelX={session.vel_x:.3f} "
            f"deltaX={(client_pos_x - session.pos_x):.3f}"
        )

        await self.send_snapshot(websocket, session, reject_reason)

    async def send_snapshot(self, websocket: Any, session: ClientSession, reject_reason: str) -> None:
        snapshot = {
            "tick": self.tick,
            "lastProcessedSeq": session.last_seq,
            "acceptedState": session.accepted_state,
            "acceptedGrounded": session.accepted_grounded,
            "acceptedJumpCount": session.accepted_jump_count,
            "acceptedDrop": session.accepted_drop,
            "serverPosX": session.pos_x,
            "serverPosY": session.pos_y,
            "serverVelX": session.vel_x,
            "serverVelY": session.vel_y,
            "rejectReason": reject_reason,
        }
        response = {
            "type": "SNAPSHOT",
            "roomId": session.room_id,
            "clientId": session.client_id,
            "payload": json.dumps(snapshot, ensure_ascii=False),
        }
        await self.send_json(websocket, response)

    async def handle_chat(self, websocket: Any, data: Dict[str, Any]) -> None:
        session = self.sessions.get(websocket)
        if session is None or not session.room_id or not session.client_id:
            await self.send_error(websocket, "请先 JOIN_ROOM，再发送 CHAT")
            return

        text = str(data.get("text", "")).strip()
        if not text:
            await self.send_error(websocket, "CHAT 缺少 text")
            return

        payload = {
            "type": "SERVER_BROADCAST",
            "roomId": session.room_id,
            "fromClientId": session.client_id,
            "text": text,
            "timestamp": self.utc_now_iso(),
        }

        for peer in self.rooms.get(session.room_id, set()):
            if peer is not websocket:
                await self.send_json(peer, payload)

    async def handle_leave_room(self, websocket: Any, data: Dict[str, Any]) -> None:
        session = self.sessions.get(websocket)
        if session is None or not session.room_id:
            await self.send_error(websocket, "当前连接尚未加入任何房间")
            return

        room_id = session.room_id
        self.remove_from_room(websocket, room_id)
        session.room_id = None

    async def cleanup_client(self, websocket: Any, reason: str) -> None:
        session = self.sessions.pop(websocket, None)
        if session is None:
            return
        if session.room_id:
            self.remove_from_room(websocket, session.room_id)

    def remove_from_room(self, websocket: Any, room_id: str) -> None:
        members = self.rooms.get(room_id)
        if not members:
            return
        members.discard(websocket)
        if not members:
            self.rooms.pop(room_id, None)

    async def send_error(self, websocket: Any, error_message: str) -> None:
        await self.send_json(websocket, {"type": "ERROR", "error": error_message})

    async def send_json(self, websocket: Any, payload: Dict[str, Any]) -> None:
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
        except ConnectionClosed:
            pass

    def hits_wall(self, x: float, y: float) -> bool:
        return game_simulation.hits_wall(x, y)

    def step_vertical(self, session: ClientSession) -> None:
        game_simulation.step_vertical(session)

    def get_standing_platform(self, session: ClientSession) -> Optional[Platform]:
        return game_simulation.get_standing_platform(session)

    def is_on_platform(self, x: float, y: float, platform: Platform) -> bool:
        return game_simulation.is_on_platform(x, y, platform)

    def find_landing_platform(self, x: float, previous_y: float, next_y: float) -> Optional[Platform]:
        return game_simulation.find_landing_platform(x, previous_y, next_y)

    @staticmethod
    def utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
