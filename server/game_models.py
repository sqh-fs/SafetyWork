from dataclasses import dataclass, field
from typing import List, Optional, Set

from game_config import (
    DEFAULT_BASE_KNOCKBACK,
    DEFAULT_KNOCKBACK_GROWTH,
    DEFAULT_WEIGHT,
    GROUND_Y,
)


@dataclass
class ServerLoot:
    loot_id: str
    loot_type: str          # "effect" / "weapon"
    item_id: str            # effectId 或 weaponId
    pos_x: float
    pos_y: float            # 空投中心 y，不是 footY
    radius: float = 0.75
    alive: bool = True

    vel_y: float = 0.0
    landed: bool = False
    target_platform_y: float = 0.0

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

    pos_x: float = 0.0
    pos_y: float = GROUND_Y
    vel_x: float = 0.0
    vel_y: float = 0.0

    facing: int = 1
    aim_x: float = 1.0
    aim_y: float = 0.0
    stocks: int = 3
    is_dead: bool = False
    respawn_at_tick: int = -1
    damage_percent: float = 0.0
    weight: float = DEFAULT_WEIGHT
    knockback_growth: float = DEFAULT_KNOCKBACK_GROWTH
    base_knockback: float = DEFAULT_BASE_KNOCKBACK

    last_knockback_x: float = 0.0
    last_knockback_y: float = 0.0
    last_hit_tick: int = -1
    hitstun_until_tick: int = -1
    equipped_weapon_id: str = "手枪"
    equipped_effect_ids: List[str] = field(default_factory=list)

    attack_hold_ticks: int = 0
    last_attack_tick: int = -999999
    last_attack_weapon_id: str = ""



@dataclass
class ServerProjectile:
    proj_id: int
    owner_client_id: str
    weapon_id: str
    effect_ids: List[str]

    pos_x: float
    pos_y: float
    vel_x: float
    vel_y: float

    radius: float
    damage: float
    base_knockback: float
    ttl: float

    alive: bool = True
    timer: float = 0.0
    state: str = "Flying"
    bullet_id: str = ""
    visual_id: str = ""
    rotation_deg: float = 0.0


@dataclass
class ServerMeleeHitbox:
    hitbox_id: int
    owner_client_id: str
    weapon_id: str
    effect_ids: List[str]

    center_x: float
    center_y: float
    radius: float

    damage: float
    base_knockback: float
    ttl: float

    hit_once: bool = True
    hit_targets: Set[str] = field(default_factory=set)

    alive: bool = True
    timer: float = 0.0


@dataclass
class MatchEvent:
    event_type: str
    event_seq: int
    data: dict


@dataclass
class InputPayload:
    seq: int = 0
    tick: int = 0
    move_x: float = 0.0
    jump_pressed: bool = False
    down_held: bool = False
    drop_pressed: bool = False
    attack_pressed: bool = False
    attack_held: bool = False
    attack_released: bool = False
    aim_x: float = 0.0
    aim_y: float = 0.0
    client_state: str = "Unknown"
    client_grounded: bool = False
    client_jump_count: int = 0
    client_pos_x: float = 0.0
    client_pos_y: float = 0.0
    client_vel_x: float = 0.0
    client_vel_y: float = 0.0
    equipped_weapon_id: str = "手枪"
    equipped_effect_ids: List[str] = field(default_factory=list)