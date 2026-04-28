from math import cos, radians, sin, atan2
from typing import Dict, List

from game_config import EFFECT_DB
from game_models import ServerProjectile

try:
    from game_config import DEBUG_PROJECTILE, DEBUG_ATTACK, DEBUG_HIT
except ImportError:
    DEBUG_PROJECTILE = False
    DEBUG_ATTACK = False
    DEBUG_HIT = True
# ------------------------------------------------------------
# Effect id 兼容层
# 解决 Unity 发 hoversplit / delayedexplosion / swordwave
# 但服务器 DB 写 hover_split / delayed_explosion / sword_wave 的问题
# ------------------------------------------------------------

EFFECT_ID_ALIASES = {
    "hoversplit": "hover_split",
    "hover_split": "hover_split",
    "Effect_HoverSplit": "hover_split",

    "delayedexplosion": "delayed_explosion",
    "delayed_explosion": "delayed_explosion",
    "Effect_DelayedExplosion": "delayed_explosion",

    "swordwave": "sword_wave",
    "sword_wave": "sword_wave",
    "Effect_SwordWave": "sword_wave",

    "parry": "parry",
    "Effect_Parry": "parry",
}

def debug_print(enabled: bool, message: str) -> None:
    if enabled:
        print(message)
def normalize_effect_id(effect_id: str) -> str:
    if effect_id is None:
        return ""

    return EFFECT_ID_ALIASES.get(effect_id, effect_id)


def normalize_effect_list(effect_ids: List[str]) -> List[str]:
    result = []

    if effect_ids is None:
        return result

    for effect_id in effect_ids:
        normalized = normalize_effect_id(effect_id)
        if normalized and normalized not in result:
            result.append(normalized)

    return result


def get_effect_cfg(effect_id: str):
    normalized = normalize_effect_id(effect_id)
    return normalized, EFFECT_DB.get(normalized)


# ------------------------------------------------------------
# Projectile spawned
# ------------------------------------------------------------

def apply_effects_on_projectile_spawned(combat_runtime, sessions, proj: ServerProjectile) -> None:
    """
    子弹生成时初始化 effect runtime state。

    HoverSplit 在 Unity 原版里是：
    - 记录初始速度
    - slowDuration 内 Lerp 到 0
    - 然后分裂
    所以服务器也要在生成时记录初始速度。
    """
    proj.effect_ids = normalize_effect_list(proj.effect_ids)

    if "hover_split" in proj.effect_ids:
        proj.hover_split_initialized = True
        proj.hover_split_start_vel_x = proj.vel_x
        proj.hover_split_start_vel_y = proj.vel_y

        speed = (proj.vel_x * proj.vel_x + proj.vel_y * proj.vel_y) ** 0.5
        proj.hover_split_start_speed = speed

        if speed > 0.0001:
            proj.hover_split_base_dir_x = proj.vel_x / speed
            proj.hover_split_base_dir_y = proj.vel_y / speed
        else:
            proj.hover_split_base_dir_x = 1.0
            proj.hover_split_base_dir_y = 0.0

        proj.hover_split_done = False

        debug_print(
            DEBUG_PROJECTILE,
            f"[EFFECT HOVER INIT] "
            f"projId={proj.proj_id} "
            f"startVel=({proj.vel_x:.2f},{proj.vel_y:.2f}) "
            f"speed={speed:.2f} "
            f"bulletId={getattr(proj, 'bullet_id', '')} "
            f"visualId={getattr(proj, 'visual_id', '')}"
        )


# ------------------------------------------------------------
# Attack execute effects
# ------------------------------------------------------------

def apply_effects_on_attack_execute(
    combat_runtime,
    sessions,
    attacker,
    aim_x: float,
    aim_y: float,
    weapon_id: str,
    effect_ids: List[str],
    tick: int,
) -> None:
    effect_ids = normalize_effect_list(effect_ids)

    for effect_id in effect_ids:
        cfg = EFFECT_DB.get(effect_id)
        if cfg is None:
            continue

        if cfg.get("hook", "") != "on_attack_execute":
            continue

        if effect_id == "parry":
            execute_parry(combat_runtime, sessions, attacker, cfg)

        elif effect_id == "sword_wave":
            execute_sword_wave(
                combat_runtime,
                attacker,
                aim_x,
                aim_y,
                weapon_id,
                effect_ids,
                cfg,
            )


# ------------------------------------------------------------
# Projectile before move
# ------------------------------------------------------------

def apply_projectile_effects_before_move(combat_runtime, sessions, proj: ServerProjectile) -> None:
    """
    子弹飞行前：
    - hover_split 线性减速到 0

    注意：
    原 Unity 版不是 vel *= 0.92，而是：
    rb.linearVelocity = Vector2.Lerp(startVelocity, Vector2.zero, timer / slowDuration)
    """
    proj.effect_ids = normalize_effect_list(proj.effect_ids)

    for effect_id in proj.effect_ids:
        cfg = EFFECT_DB.get(effect_id)
        if cfg is None:
            continue

        if cfg.get("hook", "") != "on_projectile_spawned":
            continue

        if effect_id == "hover_split":
            slow_duration = max(0.0001, float(cfg["slow_duration"]))

            if getattr(proj, "hover_split_done", False):
                continue

            if not getattr(proj, "hover_split_initialized", False):
                # 保险：如果某颗子弹是旧逻辑生成，没走 apply_effects_on_projectile_spawned，也能初始化。
                proj.hover_split_initialized = True
                proj.hover_split_start_vel_x = proj.vel_x
                proj.hover_split_start_vel_y = proj.vel_y

                speed = (proj.vel_x * proj.vel_x + proj.vel_y * proj.vel_y) ** 0.5
                proj.hover_split_start_speed = speed

                if speed > 0.0001:
                    proj.hover_split_base_dir_x = proj.vel_x / speed
                    proj.hover_split_base_dir_y = proj.vel_y / speed
                else:
                    proj.hover_split_base_dir_x = 1.0
                    proj.hover_split_base_dir_y = 0.0

            t = min(1.0, proj.timer / slow_duration)

            start_vx = float(getattr(proj, "hover_split_start_vel_x", proj.vel_x))
            start_vy = float(getattr(proj, "hover_split_start_vel_y", proj.vel_y))

            proj.vel_x = start_vx * (1.0 - t)
            proj.vel_y = start_vy * (1.0 - t)

            if abs(proj.vel_x) > 0.0001 or abs(proj.vel_y) > 0.0001:
                proj.rotation_deg = atan2(proj.vel_y, proj.vel_x) * 180.0 / 3.141592653589793

            return


# ------------------------------------------------------------
# Projectile after move
# ------------------------------------------------------------

def apply_projectile_effects_after_move(combat_runtime, sessions, proj: ServerProjectile, tick: int) -> None:
    """
    子弹飞行后：
    - delayed_explosion 时间到爆
    - hover_split 时间到分裂
    """
    proj.effect_ids = normalize_effect_list(proj.effect_ids)

    for effect_id in proj.effect_ids:
        cfg = EFFECT_DB.get(effect_id)
        if cfg is None:
            continue

        if cfg.get("hook", "") != "on_projectile_spawned":
            continue

        if effect_id == "delayed_explosion":
            if proj.timer >= float(cfg["delay_time"]):
                trigger_delayed_explosion(
                    combat_runtime,
                    sessions,
                    proj,
                    cfg,
                    tick,
                    reason="timer",
                )
                proj.alive = False
                return

        elif effect_id == "hover_split":
            slow_duration = float(cfg["slow_duration"])

            if proj.timer >= slow_duration and not getattr(proj, "hover_split_done", False):
                trigger_hover_split(combat_runtime, proj, cfg)

                proj.hover_split_done = True
                proj.state = "SplitDone"
                proj.alive = False

                combat_runtime.push_event(
                    "PROJECTILE_DESTROYED",
                    {
                        "projId": proj.proj_id,
                        "reason": "hover_split",
                    },
                )
                return


# ------------------------------------------------------------
# Projectile hit world / player
# ------------------------------------------------------------

def apply_effects_on_projectile_world_hit(combat_runtime, sessions, proj: ServerProjectile, tick: int) -> bool:
    """
    返回值：
    True  = 这个 hit 已经被 effect 处理，比如爆炸
    False = 外层继续普通销毁
    """
    proj.effect_ids = normalize_effect_list(proj.effect_ids)

    for effect_id in proj.effect_ids:
        cfg = EFFECT_DB.get(effect_id)
        if cfg is None:
            continue

        if effect_id == "delayed_explosion" and cfg.get("explode_on_world_hit", False):
            trigger_delayed_explosion(
                combat_runtime,
                sessions,
                proj,
                cfg,
                tick,
                reason="world",
            )
            proj.alive = False
            return True

    return False


def apply_effects_on_projectile_player_hit(combat_runtime, sessions, proj: ServerProjectile, attacker, target, tick: int) -> bool:
    """
    返回值：
    True  = 命中行为已被 effect 接管
    False = 外层继续走默认命中逻辑
    """
    proj.effect_ids = normalize_effect_list(proj.effect_ids)

    for effect_id in proj.effect_ids:
        cfg = EFFECT_DB.get(effect_id)
        if cfg is None:
            continue

        if effect_id == "delayed_explosion" and cfg.get("explode_on_player_hit", False):
            trigger_delayed_explosion(
                combat_runtime,
                sessions,
                proj,
                cfg,
                tick,
                reason="player",
            )
            proj.alive = False
            return True

    return False


# ------------------------------------------------------------
# Parry
# ------------------------------------------------------------

def execute_parry(combat_runtime, sessions, attacker, cfg: Dict) -> None:
    """
    第一版简化：
    攻击瞬间检查附近 projectile 并反弹。
    """
    radius = 1.2
    speed_mul = float(cfg["deflect_speed_multiplier"])

    nearby_projectiles = combat_runtime.find_projectiles_in_radius(
        center_x=attacker.pos_x,
        center_y=attacker.pos_y + 0.4,
        radius=radius,
        ignore_owner_client_id=attacker.client_id,
    )

    for proj in nearby_projectiles:
        proj.owner_client_id = attacker.client_id
        proj.vel_x = -proj.vel_x * speed_mul
        proj.vel_y = -proj.vel_y * speed_mul

        if abs(proj.vel_x) > 0.0001 or abs(proj.vel_y) > 0.0001:
            proj.rotation_deg = atan2(proj.vel_y, proj.vel_x) * 180.0 / 3.141592653589793

        combat_runtime.push_event(
            "PLAYER_PARRIED",
            {
                "clientId": attacker.client_id,
                "projId": proj.proj_id,
            },
        )
        break


# ------------------------------------------------------------
# Sword wave
# ------------------------------------------------------------

def execute_sword_wave(
    combat_runtime,
    attacker,
    aim_x: float,
    aim_y: float,
    weapon_id: str,
    effect_ids: List[str],
    cfg: Dict,
) -> None:
    mag = (aim_x * aim_x + aim_y * aim_y) ** 0.5

    if mag <= 0.0001:
        aim_x = 1.0 if attacker.facing >= 0 else -1.0
        aim_y = 0.0
        mag = 1.0

    dir_x = aim_x / mag
    dir_y = aim_y / mag

    new_effects = normalize_effect_list(effect_ids) if cfg.get("inherit_runtime_effects", True) else []
    new_effects = [
    eid for eid in new_effects
    if eid not in ("sword_wave", "hover_split", "delayed_explosion")
    ]

    damage_mul = float(cfg["damage_multiplier"])
    speed = float(cfg["speed"])

    combat_runtime.spawn_custom_projectile(
        owner_client_id=attacker.client_id,
        weapon_id=cfg.get("projectile_kind", "sword_wave"),
        effect_ids=new_effects,
        spawn_x=attacker.pos_x + dir_x * 0.8,
        spawn_y=attacker.pos_y + 0.4 + dir_y * 0.2,
        vel_x=dir_x * speed,
        vel_y=dir_y * speed,
        radius=0.22,
        damage=12.0 * damage_mul,
        base_knockback=2.0,
        ttl=1.0,
        bullet_id="剑气",
        visual_id="剑气",
    )


# ------------------------------------------------------------
# Delayed explosion
# ------------------------------------------------------------

def trigger_delayed_explosion(
    combat_runtime,
    sessions,
    proj: ServerProjectile,
    cfg: Dict,
    tick: int,
    reason: str = "",
) -> None:
    radius = float(cfg["explosion_radius"])
    damage_mul = float(cfg["damage_multiplier"])

    combat_runtime.push_event(
        "EXPLOSION_TRIGGERED",
        {
            "projId": proj.proj_id,
            "ownerClientId": proj.owner_client_id,
            "x": proj.pos_x,
            "y": proj.pos_y,
            "radius": radius,
            "reason": reason,
        },
    )

    debug_print(
        DEBUG_PROJECTILE,
        f"[SERVER EXPLOSION] "
        f"projId={proj.proj_id} "
        f"owner={proj.owner_client_id} "
        f"reason={reason} "
        f"pos=({proj.pos_x:.2f},{proj.pos_y:.2f}) "
        f"radius={radius:.2f}"
    )

    hit_targets = combat_runtime.find_players_in_radius(
        sessions=sessions,
        center_x=proj.pos_x,
        center_y=proj.pos_y,
        radius=radius,
        ignore_client_id=proj.owner_client_id,
    )

    attacker = combat_runtime.find_session_by_client_id(sessions, proj.owner_client_id)

    if attacker is not None:
        for target in hit_targets:
            combat_runtime.apply_hit(
                attacker=attacker,
                target=target,
                damage=proj.damage * damage_mul,
                base_knockback=proj.base_knockback,
                weapon_id=proj.weapon_id,
                tick=tick,
            )

    combat_runtime.push_event(
        "PROJECTILE_DESTROYED",
        {
            "projId": proj.proj_id,
            "reason": f"explosion_{reason}" if reason else "explosion",
        },
    )


# ------------------------------------------------------------
# Hover split
# ------------------------------------------------------------

def trigger_hover_split(combat_runtime, proj: ServerProjectile, cfg: Dict) -> None:
    split_count = int(cfg["split_count"])

    if split_count <= 0:
        return

    base_dir_x = float(getattr(proj, "hover_split_base_dir_x", 1.0))
    base_dir_y = float(getattr(proj, "hover_split_base_dir_y", 0.0))

    base_angle = atan2(base_dir_y, base_dir_x)

    angle_step = 360.0 / split_count
    inherit_except_self = bool(cfg.get("inherit_effects_except_self", True))

    child_effects = normalize_effect_list(list(proj.effect_ids))

    if inherit_except_self:
        child_effects = [
        eid for eid in child_effects
        if eid not in ("hover_split")
    ]

    speed = float(getattr(proj, "hover_split_start_speed", 0.0))

    if speed <= 0.0001:
        speed = (proj.vel_x * proj.vel_x + proj.vel_y * proj.vel_y) ** 0.5

    if speed <= 0.0001:
        speed = 10.0

    bullet_id = getattr(proj, "bullet_id", "")
    visual_id = getattr(proj, "visual_id", "")

    debug_print(
        DEBUG_PROJECTILE,
        f"[SERVER HOVER SPLIT] "
        f"parent={proj.proj_id} "
        f"splitCount={split_count} "
        f"bulletId={bullet_id} "
        f"visualId={visual_id} "
        f"speed={speed:.2f} "
        f"effects={child_effects}"
    )

    for i in range(split_count):
        angle_deg = i * angle_step
        angle = base_angle + radians(angle_deg)

        dir_x = cos(angle)
        dir_y = sin(angle)

        combat_runtime.spawn_custom_projectile(
            owner_client_id=proj.owner_client_id,
            weapon_id=proj.weapon_id,
            effect_ids=child_effects,
            spawn_x=proj.pos_x,
            spawn_y=proj.pos_y,
            vel_x=dir_x * speed,
            vel_y=dir_y * speed,
            radius=proj.radius,
            damage=proj.damage,
            base_knockback=proj.base_knockback,
            ttl=max(0.5, proj.ttl - 0.2),
            bullet_id=bullet_id,
            visual_id=visual_id,
            rotation_deg=angle * 180.0 / 3.141592653589793,
        )