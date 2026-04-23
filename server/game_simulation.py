from typing import List, Optional

from game_config import (
    FALL_SPEED_CAP,
    GRAVITY,
    GROUND_EPSILON,
    GROUND_Y,
    OFFSET_Y,
    PLAYER_HALF_HEIGHT,
    PLAYER_HALF_WIDTH,
    SIM_DT,
)
from game_models import ClientSession, Platform, RectCollider


MAP_PLATFORMS: List[Platform] = [
    Platform(x_min=-9, x_max=29, y=GROUND_Y, kind="solid"),
    Platform(x_min=-1.25, x_max=1.25, y=1.0 + OFFSET_Y, kind="oneway"),
    Platform(x_min=8.75, x_max=11.25, y=1.0 + OFFSET_Y, kind="oneway"),
    Platform(x_min=18.75, x_max=21.25, y=1.0 + OFFSET_Y, kind="oneway"),
    Platform(x_min=3.75, x_max=6.25, y=2.5 + OFFSET_Y, kind="oneway"),
    Platform(x_min=13.75, x_max=16.25, y=2.5 + OFFSET_Y, kind="oneway"),
]

MAP_WALLS: List[RectCollider] = [
    RectCollider(x_min=-9.0, x_max=-8.5, y_min=GROUND_Y, y_max=GROUND_Y + 1.5, kind="solid"),
    RectCollider(x_min=29.0, x_max=29.5, y_min=GROUND_Y, y_max=GROUND_Y + 1.5, kind="solid"),
]


def hits_wall(x: float, y: float) -> bool:
    """判断角色包围盒在给定位置是否与墙体重叠。"""
    player_left = x - PLAYER_HALF_WIDTH
    player_right = x + PLAYER_HALF_WIDTH
    player_bottom = y
    player_top = y + PLAYER_HALF_HEIGHT * 2.0

    for wall in MAP_WALLS:
        overlap_x = player_right > wall.x_min and player_left < wall.x_max
        overlap_y = player_top > wall.y_min and player_bottom < wall.y_max

        if overlap_x and overlap_y:
            return True

    return False


def step_vertical(session: ClientSession) -> None:
    """按原始顺序推进角色垂直速度和落地状态。"""
    standing = get_standing_platform(session)
    if standing is not None and session.accepted_grounded and session.vel_y <= 0.0:
        session.pos_y = standing.y
        session.vel_y = 0.0
        return

    session.vel_y += GRAVITY
    if session.vel_y < FALL_SPEED_CAP:
        session.vel_y = FALL_SPEED_CAP

    previous_y = session.pos_y
    next_y = session.pos_y + session.vel_y * SIM_DT

    landing = find_landing_platform(session.pos_x, previous_y, next_y)
    if landing is not None and session.vel_y <= 0:
        session.pos_y = landing.y
        session.vel_y = 0.0
        session.accepted_grounded = True
        if session.accepted_state not in ("Dash", "BasicAttack"):
            session.accepted_state = "Grounded"
    else:
        session.pos_y = next_y
        session.accepted_grounded = False
        if session.vel_y < 0 and session.accepted_state not in ("Jump", "Dash", "BasicAttack"):
            session.accepted_state = "Fall"


def get_standing_platform(session: ClientSession) -> Optional[Platform]:
    for platform in MAP_PLATFORMS:
        if is_on_platform(session.pos_x, session.pos_y, platform):
            return platform
    return None


def is_on_platform(x: float, y: float, platform: Platform) -> bool:
    within_x = (x + PLAYER_HALF_WIDTH) >= platform.x_min and (x - PLAYER_HALF_WIDTH) <= platform.x_max
    close_y = abs(y - platform.y) <= GROUND_EPSILON
    return within_x and close_y


def find_landing_platform(x: float, previous_y: float, next_y: float) -> Optional[Platform]:
    candidates: List[Platform] = []

    for platform in MAP_PLATFORMS:
        within_x = (x + PLAYER_HALF_WIDTH) >= platform.x_min and (x - PLAYER_HALF_WIDTH) <= platform.x_max
        crossed_y = previous_y >= platform.y >= next_y
        if within_x and crossed_y:
            candidates.append(platform)

    if not candidates:
        return None

    candidates.sort(key=lambda p: p.y, reverse=True)
    return candidates[0]
