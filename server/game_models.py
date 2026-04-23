from dataclasses import dataclass
from typing import Optional

from game_config import GROUND_Y


@dataclass
class Platform:
    x_min: float
    x_max: float
    y: float
    kind: str  # "solid" | "oneway"


@dataclass
class RectCollider:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    kind: str  # "solid"


@dataclass
class ClientSession:
    client_id: Optional[str] = None
    room_id: Optional[str] = None

    last_seq: int = -1
    accepted_state: str = "Grounded"
    accepted_grounded: bool = True
    accepted_jump_count: int = 0
    accepted_drop: bool = False

    # 简化服务器角色状态
    pos_x: float = 0.0
    pos_y: float = GROUND_Y
    vel_x: float = 0.0
    vel_y: float = 0.0
