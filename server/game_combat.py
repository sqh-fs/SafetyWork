from typing import Dict, List, Optional
import math

import game_effects
import game_simulation
from game_config import (
    BULLET_DB,
    MELEE_DB,
    PLAYER_HALF_HEIGHT,
    PLAYER_HALF_WIDTH,
    SIM_DT,
    WEAPON_DB,
    KNOCKBACK_SCALE,
    HITSTUN_BASE_TICKS,
    HITSTUN_PERCENT_FACTOR_TO_TICKS,MAX_PROJECTILES,
)

# Debug switches are optional.
# If game_config.py has not defined them yet, these defaults will be used.
try:
    from game_config import (
        DEBUG_COMBAT_WARN,
        DEBUG_ATTACK,
        DEBUG_PROJECTILE,
        DEBUG_HIT,
    )
except ImportError:
    DEBUG_COMBAT_WARN = True
    DEBUG_ATTACK = False
    DEBUG_PROJECTILE = False
    DEBUG_HIT = True

from game_models import (
    ClientSession,
    MatchEvent,
    ServerMeleeHitbox,
    ServerProjectile,
)


DEFAULT_WEAPON_ID = "手枪"
DEFAULT_BULLET_ID = "普通子弹"


def debug_print(enabled: bool, message: str) -> None:
    if enabled:
        print(message)


class CombatRuntime:
    def __init__(self) -> None:
        self.projectiles: Dict[int, ServerProjectile] = {}
        self.melee_hitboxes: Dict[int, ServerMeleeHitbox] = {}

        self.next_projectile_id: int = 1
        self.next_melee_hitbox_id: int = 1
        self.next_event_seq: int = 1

        self.pending_events: List[MatchEvent] = []

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def get_weapon_cfg(self, weapon_id: str) -> dict:
        if weapon_id in WEAPON_DB:
            return WEAPON_DB[weapon_id]

        if DEFAULT_WEAPON_ID in WEAPON_DB:
            debug_print(
                DEBUG_COMBAT_WARN,
                f"[COMBAT WARN] weapon_id={weapon_id} not found, fallback={DEFAULT_WEAPON_ID}"
            )
            return WEAPON_DB[DEFAULT_WEAPON_ID]

        first_key = next(iter(WEAPON_DB))
        debug_print(
            DEBUG_COMBAT_WARN,
            f"[COMBAT WARN] DEFAULT_WEAPON_ID missing, fallback first weapon={first_key}"
        )
        return WEAPON_DB[first_key]

    def get_bullet_cfg(self, bullet_id: str) -> dict:
        if bullet_id in BULLET_DB:
            return BULLET_DB[bullet_id]

        if DEFAULT_BULLET_ID in BULLET_DB:
            debug_print(
                DEBUG_COMBAT_WARN,
                f"[COMBAT WARN] bullet_id={bullet_id} not found, fallback={DEFAULT_BULLET_ID}"
            )
            return BULLET_DB[DEFAULT_BULLET_ID]

        first_key = next(iter(BULLET_DB))
        debug_print(
            DEBUG_COMBAT_WARN,
            f"[COMBAT WARN] DEFAULT_BULLET_ID missing, fallback first bullet={first_key}"
        )
        return BULLET_DB[first_key]

    def get_weapon_bullet_id(self, weapon_cfg: dict) -> str:
        return str(weapon_cfg.get("bullet_id", DEFAULT_BULLET_ID))

    def resolve_visual_id(self, bullet_id: str, bullet_cfg: dict) -> str:
        return str(bullet_cfg.get("visual_id", bullet_id))

    def normalize_special_bullet_id(self, bullet_id: str) -> str:
        """
        兼容旧 effect 里可能传进来的英文 projectile_kind。
        """
        if bullet_id in ("sword_wave", "swordwave", "SwordWave"):
            return "剑气"

        if bullet_id in ("pistol_bullet", "normal_gun"):
            return "普通子弹"

        if bullet_id in ("sniper_bullet", "sniper"):
            return "狙击子弹"

        if bullet_id in ("heavy_machine_bullet", "machine_gun"):
            return "机枪子弹"

        return bullet_id

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def push_event(self, event_type: str, data: dict) -> None:
        self.pending_events.append(
            MatchEvent(
                event_type=event_type,
                event_seq=self.next_event_seq,
                data=data,
            )
        )
        self.next_event_seq += 1

    def clear_events(self) -> None:
        self.pending_events.clear()

    # ------------------------------------------------------------------
    # Attack entry
    # ------------------------------------------------------------------

    def execute_attack(
        self,
        attacker: ClientSession,
        aim_x: float,
        aim_y: float,
        tick: int,
        sessions: Dict[object, ClientSession],
    ) -> None:
        if attacker is None or attacker.client_id is None or attacker.is_dead:
            return

        weapon_cfg = self.get_weapon_cfg(attacker.equipped_weapon_id)
        attack_mode = weapon_cfg.get("attack_mode", "ranged")

        debug_print(
            DEBUG_ATTACK,
            f"[SERVER ATTACK] "
            f"client={attacker.client_id} "
            f"weapon={attacker.equipped_weapon_id} "
            f"mode={attack_mode} "
            f"effects={attacker.equipped_effect_ids} "
            f"aim=({aim_x:.2f},{aim_y:.2f})"
        )

        if attack_mode == "melee":
            melee_profile = weapon_cfg.get("melee_profile", attacker.equipped_weapon_id)
            self.spawn_melee_hitbox(
                attacker=attacker,
                melee_profile=melee_profile,
                aim_x=aim_x,
                aim_y=aim_y,
            )
        else:
            self.spawn_projectile(attacker, aim_x, aim_y)

        game_effects.apply_effects_on_attack_execute(
            self,
            sessions,
            attacker,
            aim_x,
            aim_y,
            attacker.equipped_weapon_id,
            attacker.equipped_effect_ids,
            tick,
        )

    # ------------------------------------------------------------------
    # Aim helpers
    # ------------------------------------------------------------------

    def normalize_aim(self, owner: ClientSession, aim_x: float, aim_y: float) -> tuple[float, float]:
        mag = math.sqrt(aim_x * aim_x + aim_y * aim_y)

        if mag <= 0.0001:
            aim_x = 1.0 if owner.facing >= 0 else -1.0
            aim_y = 0.0
            mag = 1.0

        return aim_x / mag, aim_y / mag

    # ------------------------------------------------------------------
    # Projectile spawning
    # ------------------------------------------------------------------

    def spawn_projectile(self, owner: ClientSession, aim_x: float, aim_y: float) -> None:
        if owner.is_dead or owner.client_id is None:
            return

        dir_x, dir_y = self.normalize_aim(owner, aim_x, aim_y)

        weapon_cfg = self.get_weapon_cfg(owner.equipped_weapon_id)
        bullet_id = self.get_weapon_bullet_id(weapon_cfg)
        bullet_id = self.normalize_special_bullet_id(bullet_id)

        bullet_cfg = self.get_bullet_cfg(bullet_id)

        pellet_count = int(weapon_cfg.get("pellet_count", 1))
        spread_angle_deg = float(weapon_cfg.get("spread_angle_deg", 0.0))

        if pellet_count < 1:
            pellet_count = 1

        base_angle_rad = math.atan2(dir_y, dir_x)

        if pellet_count == 1:
            angle_offsets_deg = [0.0]
        else:
            start_angle = -spread_angle_deg * 0.5
            step = spread_angle_deg / max(1, pellet_count - 1)
            angle_offsets_deg = [start_angle + step * i for i in range(pellet_count)]

        debug_print(
            DEBUG_PROJECTILE,
            f"[SERVER BULLET CFG] "
            f"weapon={owner.equipped_weapon_id} "
            f"bulletId={bullet_id} "
            f"pelletCount={pellet_count} "
            f"spread={spread_angle_deg} "
            f"bulletCfg={bullet_cfg}"
        )

        for offset_deg in angle_offsets_deg:
            angle_rad = base_angle_rad + math.radians(offset_deg)

            shot_dir_x = math.cos(angle_rad)
            shot_dir_y = math.sin(angle_rad)
            if len(self.projectiles) >= MAX_PROJECTILES:
                return
            self._spawn_one_projectile(
                owner_client_id=owner.client_id,
                weapon_id=owner.equipped_weapon_id,
                effect_ids=list(owner.equipped_effect_ids),
                owner_pos_x=owner.pos_x,
                owner_pos_y=owner.pos_y,
                dir_x=shot_dir_x,
                dir_y=shot_dir_y,
                weapon_cfg=weapon_cfg,
                bullet_id=bullet_id,
                bullet_cfg=bullet_cfg,
            )

    def _spawn_one_projectile(
        self,
        owner_client_id: str,
        weapon_id: str,
        effect_ids: List[str],
        owner_pos_x: float,
        owner_pos_y: float,
        dir_x: float,
        dir_y: float,
        weapon_cfg: dict,
        bullet_id: str,
        bullet_cfg: dict,
    ) -> None:
        speed = float(bullet_cfg.get("speed", weapon_cfg.get("projectile_speed", 18.0)))
        radius = float(bullet_cfg.get("radius", weapon_cfg.get("projectile_radius", 0.2)))
        ttl = float(bullet_cfg.get("ttl", weapon_cfg.get("projectile_ttl", 2.0)))

        damage = float(
            bullet_cfg.get(
                "base_damage",
                weapon_cfg.get("base_damage", 10.0),
            )
        )

        base_knockback = float(
            bullet_cfg.get(
                "base_knockback",
                weapon_cfg.get("base_knockback", 2.0),
            )
        )

        visual_id = self.resolve_visual_id(bullet_id, bullet_cfg)

        spawn_forward = float(
            bullet_cfg.get(
                "spawn_forward",
                weapon_cfg.get("spawn_forward", 0.6),
            )
        )

        spawn_up = float(
            bullet_cfg.get(
                "spawn_up",
                weapon_cfg.get("spawn_up", 0.4),
            )
        )

        spawn_aim_up = float(
            bullet_cfg.get(
                "spawn_aim_up",
                weapon_cfg.get("spawn_aim_up", 0.2),
            )
        )

        rotation_deg = math.degrees(math.atan2(dir_y, dir_x))

        proj = ServerProjectile(
            proj_id=self.next_projectile_id,
            owner_client_id=owner_client_id,
            weapon_id=weapon_id,
            effect_ids=list(effect_ids),
            pos_x=owner_pos_x + dir_x * spawn_forward,
            pos_y=owner_pos_y + spawn_up + dir_y * spawn_aim_up,
            vel_x=dir_x * speed,
            vel_y=dir_y * speed,
            radius=radius,
            damage=damage,
            base_knockback=base_knockback,
            ttl=ttl,
        )

        proj.bullet_id = bullet_id
        proj.visual_id = visual_id
        proj.rotation_deg = rotation_deg

        self.projectiles[proj.proj_id] = proj
        self.next_projectile_id += 1

        self.push_event(
            "PROJECTILE_SPAWNED",
            {
                "projId": proj.proj_id,
                "ownerClientId": proj.owner_client_id,
                "weaponId": proj.weapon_id,
                "bulletId": bullet_id,
                "visualId": visual_id,
                "x": proj.pos_x,
                "y": proj.pos_y,
                "velX": proj.vel_x,
                "velY": proj.vel_y,
                "rotationDeg": rotation_deg,
                "radius": radius,
            },
        )

        debug_print(
            DEBUG_PROJECTILE,
            f"[SERVER PROJECTILE SPAWN] "
            f"projId={proj.proj_id} "
            f"owner={proj.owner_client_id} "
            f"weapon={proj.weapon_id} "
            f"bulletId={bullet_id} "
            f"visualId={visual_id} "
            f"effects={proj.effect_ids} "
            f"pos=({proj.pos_x:.2f},{proj.pos_y:.2f}) "
            f"vel=({proj.vel_x:.2f},{proj.vel_y:.2f}) "
            f"rot={rotation_deg:.1f}"
        )

        game_effects.apply_effects_on_projectile_spawned(self, None, proj)

    def spawn_custom_projectile(
        self,
        owner_client_id: str,
        weapon_id: str,
        effect_ids: List[str],
        spawn_x: float,
        spawn_y: float,
        vel_x: float,
        vel_y: float,
        radius: float,
        damage: float,
        base_knockback: float,
        ttl: float,
        bullet_id: str = "",
        visual_id: str = "",
        rotation_deg: Optional[float] = None,
    ) -> None:
        if not bullet_id:
            bullet_id = weapon_id

        bullet_id = self.normalize_special_bullet_id(bullet_id)
        bullet_cfg = self.get_bullet_cfg(bullet_id)
        if len(self.projectiles) >= MAX_PROJECTILES:
            return
        if not visual_id:
            visual_id = self.resolve_visual_id(bullet_id, bullet_cfg)

        if rotation_deg is None:
            if abs(vel_x) <= 0.0001 and abs(vel_y) <= 0.0001:
                rotation_deg = 0.0
            else:
                rotation_deg = math.degrees(math.atan2(vel_y, vel_x))
  
        proj = ServerProjectile(
            proj_id=self.next_projectile_id,
            owner_client_id=owner_client_id,
            weapon_id=weapon_id,
            effect_ids=list(effect_ids),
            pos_x=spawn_x,
            pos_y=spawn_y,
            vel_x=vel_x,
            vel_y=vel_y,
            radius=radius,
            damage=damage,
            base_knockback=base_knockback,
            ttl=ttl,
        )

        proj.bullet_id = bullet_id
        proj.visual_id = visual_id
        proj.rotation_deg = rotation_deg

        self.projectiles[proj.proj_id] = proj
        self.next_projectile_id += 1

        self.push_event(
            "PROJECTILE_SPAWNED",
            {
                "projId": proj.proj_id,
                "ownerClientId": proj.owner_client_id,
                "weaponId": proj.weapon_id,
                "bulletId": bullet_id,
                "visualId": visual_id,
                "x": proj.pos_x,
                "y": proj.pos_y,
                "velX": proj.vel_x,
                "velY": proj.vel_y,
                "rotationDeg": rotation_deg,
                "radius": radius,
            },
        )

        debug_print(
            DEBUG_PROJECTILE,
            f"[SERVER CUSTOM PROJECTILE] "
            f"projId={proj.proj_id} "
            f"owner={proj.owner_client_id} "
            f"weapon={proj.weapon_id} "
            f"bulletId={bullet_id} "
            f"visualId={visual_id} "
            f"effects={proj.effect_ids} "
            f"pos=({proj.pos_x:.2f},{proj.pos_y:.2f}) "
            f"vel=({proj.vel_x:.2f},{proj.vel_y:.2f}) "
            f"rot={rotation_deg:.1f}"
        )

        game_effects.apply_effects_on_projectile_spawned(self, None, proj)

    # ------------------------------------------------------------------
    # Melee hitbox
    # ------------------------------------------------------------------

    def spawn_melee_hitbox(
        self,
        attacker: ClientSession,
        melee_profile: str,
        aim_x: float,
        aim_y: float,
    ) -> None:
        cfg = MELEE_DB.get(melee_profile)
        if cfg is None or attacker.client_id is None:
            debug_print(DEBUG_COMBAT_WARN, f"[COMBAT WARN] melee_profile={melee_profile} not found.")
            return

        dir_x = aim_x
        dir_y = aim_y
        mag = math.sqrt(dir_x * dir_x + dir_y * dir_y)

        if mag <= 0.0001:
            dir_x = 1.0 if attacker.facing >= 0 else -1.0
            dir_y = 0.0
            mag = 1.0

        dir_x /= mag
        dir_y /= mag

        center_x = attacker.pos_x + dir_x * float(cfg["offset_x"])
        center_y = attacker.pos_y + float(cfg["offset_y"]) + dir_y * 0.15

        hitbox = ServerMeleeHitbox(
            hitbox_id=self.next_melee_hitbox_id,
            owner_client_id=attacker.client_id,
            weapon_id=melee_profile,
            effect_ids=list(attacker.equipped_effect_ids),
            center_x=center_x,
            center_y=center_y,
            radius=float(cfg["radius"]),
            damage=float(cfg["base_damage"]),
            base_knockback=float(cfg["base_knockback"]),
            ttl=float(cfg["active_time"]),
            hit_once=bool(cfg.get("hit_once", True)),
        )

        self.melee_hitboxes[hitbox.hitbox_id] = hitbox
        self.next_melee_hitbox_id += 1

        self.push_event(
            "MELEE_HITBOX_SPAWNED",
            {
                "hitboxId": hitbox.hitbox_id,
                "ownerClientId": hitbox.owner_client_id,
                "weaponId": hitbox.weapon_id,
                "x": hitbox.center_x,
                "y": hitbox.center_y,
                "radius": hitbox.radius,
            },
        )

        debug_print(
            DEBUG_ATTACK,
            f"[SERVER MELEE SPAWN] "
            f"hitboxId={hitbox.hitbox_id} "
            f"owner={hitbox.owner_client_id} "
            f"weapon={hitbox.weapon_id} "
            f"pos=({hitbox.center_x:.2f},{hitbox.center_y:.2f}) "
            f"radius={hitbox.radius:.2f}"
        )

    # ------------------------------------------------------------------
    # Collision helpers
    # ------------------------------------------------------------------

    def segment_intersects_aabb(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        left: float,
        right: float,
        bottom: float,
        top: float,
    ) -> bool:
        dx = x2 - x1
        dy = y2 - y1

        t_min = 0.0
        t_max = 1.0

        if abs(dx) < 0.000001:
            if x1 < left or x1 > right:
                return False
        else:
            inv_dx = 1.0 / dx
            t1 = (left - x1) * inv_dx
            t2 = (right - x1) * inv_dx

            if t1 > t2:
                t1, t2 = t2, t1

            t_min = max(t_min, t1)
            t_max = min(t_max, t2)

            if t_min > t_max:
                return False

        if abs(dy) < 0.000001:
            if y1 < bottom or y1 > top:
                return False
        else:
            inv_dy = 1.0 / dy
            t1 = (bottom - y1) * inv_dy
            t2 = (top - y1) * inv_dy

            if t1 > t2:
                t1, t2 = t2, t1

            t_min = max(t_min, t1)
            t_max = min(t_max, t2)

            if t_min > t_max:
                return False

        return True

    def projectile_swept_hits_world(
        self,
        old_x: float,
        old_y: float,
        next_x: float,
        next_y: float,
        radius: float,
    ) -> bool:
        for wall in game_simulation.MAP_WALLS:
            left = wall.x_min - radius
            right = wall.x_max + radius
            bottom = wall.y_min - radius
            top = wall.y_max + radius

            if self.segment_intersects_aabb(
                old_x,
                old_y,
                next_x,
                next_y,
                left,
                right,
                bottom,
                top,
            ):
                return True

        for platform in game_simulation.MAP_PLATFORMS:
            left = platform.x_min - radius
            right = platform.x_max + radius
            bottom = platform.y - radius
            top = platform.y + radius

            if self.segment_intersects_aabb(
                old_x,
                old_y,
                next_x,
                next_y,
                left,
                right,
                bottom,
                top,
            ):
                return True

        return False

    def projectile_hits_world(self, x: float, y: float, radius: float) -> bool:
        left = x - radius
        right = x + radius
        bottom = y - radius
        top = y + radius

        for wall in game_simulation.MAP_WALLS:
            overlap_x = right > wall.x_min and left < wall.x_max
            overlap_y = top > wall.y_min and bottom < wall.y_max
            if overlap_x and overlap_y:
                return True

        for platform in game_simulation.MAP_PLATFORMS:
            overlap_x = right > platform.x_min and left < platform.x_max
            overlap_y = abs(y - platform.y) <= radius
            if overlap_x and overlap_y:
                return True

        return False

    def find_projectile_swept_hit_player(
        self,
        sessions: Dict[object, ClientSession],
        old_x: float,
        old_y: float,
        next_x: float,
        next_y: float,
        radius: float,
        owner_client_id: str,
    ) -> Optional[ClientSession]:
        for session in sessions.values():
            if session.client_id is None:
                continue

            if session.client_id == owner_client_id:
                continue

            if session.is_dead:
                continue

            left = session.pos_x - PLAYER_HALF_WIDTH - radius
            right = session.pos_x + PLAYER_HALF_WIDTH + radius
            bottom = session.pos_y - radius
            top = session.pos_y + PLAYER_HALF_HEIGHT * 2.0 + radius

            if self.segment_intersects_aabb(
                old_x,
                old_y,
                next_x,
                next_y,
                left,
                right,
                bottom,
                top,
            ):
                debug_print(
                    DEBUG_PROJECTILE,
                    f"[SERVER SWEPT PLAYER HIT] "
                    f"owner={owner_client_id} "
                    f"target={session.client_id} "
                    f"from=({old_x:.2f},{old_y:.2f}) "
                    f"to=({next_x:.2f},{next_y:.2f}) "
                    f"aabb=({left:.2f},{right:.2f},{bottom:.2f},{top:.2f})"
                )
                return session

        return None

    def find_projectile_hit_player(
        self,
        sessions: Dict[object, ClientSession],
        x: float,
        y: float,
        radius: float,
        owner_client_id: str,
    ) -> Optional[ClientSession]:
        for session in sessions.values():
            if session.client_id is None:
                continue

            if session.client_id == owner_client_id:
                continue

            if session.is_dead:
                continue

            player_left = session.pos_x - PLAYER_HALF_WIDTH
            player_right = session.pos_x + PLAYER_HALF_WIDTH
            player_bottom = session.pos_y
            player_top = session.pos_y + PLAYER_HALF_HEIGHT * 2.0

            proj_left = x - radius
            proj_right = x + radius
            proj_bottom = y - radius
            proj_top = y + radius

            overlap_x = proj_right > player_left and proj_left < player_right
            overlap_y = proj_top > player_bottom and proj_bottom < player_top

            if overlap_x and overlap_y:
                return session

        return None

    def find_session_by_client_id(
        self,
        sessions: Dict[object, ClientSession],
        client_id: str,
    ) -> Optional[ClientSession]:
        for session in sessions.values():
            if session.client_id == client_id:
                return session

        return None

    def find_players_in_radius(
        self,
        sessions: Dict[object, ClientSession],
        center_x: float,
        center_y: float,
        radius: float,
        ignore_client_id: Optional[str] = None,
    ) -> List[ClientSession]:
        result: List[ClientSession] = []
        rr = radius * radius

        for session in sessions.values():
            if session.client_id is None or session.is_dead:
                continue

            if ignore_client_id is not None and session.client_id == ignore_client_id:
                continue

            dx = session.pos_x - center_x
            dy = (session.pos_y + 0.4) - center_y

            if dx * dx + dy * dy <= rr:
                result.append(session)

        return result

    def find_projectiles_in_radius(
        self,
        center_x: float,
        center_y: float,
        radius: float,
        ignore_owner_client_id: Optional[str] = None,
    ) -> List[ServerProjectile]:
        result: List[ServerProjectile] = []
        rr = radius * radius

        for proj in self.projectiles.values():
            if not proj.alive:
                continue

            if ignore_owner_client_id is not None and proj.owner_client_id == ignore_owner_client_id:
                continue

            dx = proj.pos_x - center_x
            dy = proj.pos_y - center_y

            if dx * dx + dy * dy <= rr:
                result.append(proj)

        return result

    # ------------------------------------------------------------------
    # Damage / knockback
    # ------------------------------------------------------------------

    def apply_hit(
        self,
        attacker: ClientSession,
        target: ClientSession,
        damage: float,
        base_knockback: float,
        weapon_id: str,
        tick: int,
    ) -> None:
        if target.is_dead:
            return

        final_damage = max(0.0, float(damage))

        # 命中前百分比：用于大乱斗击飞公式
        damage_before_hit = max(0.0, float(target.damage_percent))

        # 判定伤害来源方向：target 在 attacker 右边，则往右飞；否则往左飞
        direction_x = 1.0 if target.pos_x > attacker.pos_x else -1.0

        # 大乱斗式击飞方向：水平来源方向 + 向上
        knockback_dir_x = direction_x
        knockback_dir_y = 1.0

        mag = (knockback_dir_x * knockback_dir_x + knockback_dir_y * knockback_dir_y) ** 0.5

        if mag <= 0.0001:
            knockback_dir_x = direction_x
            knockback_dir_y = 0.0
            mag = 1.0

        knockback_dir_x /= mag
        knockback_dir_y /= mag

        safe_weight = max(1.0, float(target.weight))
        knockback_growth = float(target.knockback_growth)

        # percentageFactor = 当前百分比 * 本次伤害 * 放大系数 / 体重
        percentage_factor = (damage_before_hit * final_damage * knockback_growth) / safe_weight

        base_force = max(0.0, float(base_knockback))

        # 这里要用总缩放，不然手感容易爆炸
        final_force = (base_force + percentage_factor) * KNOCKBACK_SCALE

        knockback_x = knockback_dir_x * final_force
        knockback_y = knockback_dir_y * final_force

        # 再结算伤害百分比
        target.damage_percent = damage_before_hit + final_damage

        # 计算受击硬直 tick
        hitstun_ticks = int(
            HITSTUN_BASE_TICKS + percentage_factor * HITSTUN_PERCENT_FACTOR_TO_TICKS
        )
        hitstun_ticks = max(HITSTUN_BASE_TICKS, hitstun_ticks)

        # 服务器权威击退状态
        target.vel_x = knockback_x
        target.vel_y = knockback_y

        target.last_knockback_x = knockback_x
        target.last_knockback_y = knockback_y
        target.last_hit_tick = tick

        target.hitstun_until_tick = tick + hitstun_ticks

        target.accepted_state = "Hitstun"
        target.accepted_grounded = False

        self.push_event(
            "PLAYER_HIT",
            {
                "attackerClientId": attacker.client_id,
                "targetClientId": target.client_id,
                "weaponId": weapon_id,
                "damageAdded": final_damage,
                "damageBefore": damage_before_hit,
                "newDamagePercent": target.damage_percent,
                "percentageFactor": percentage_factor,
                "baseKnockback": base_force,
                "knockbackScale": KNOCKBACK_SCALE,
                "finalKnockbackForce": final_force,
                "knockbackX": knockback_x,
                "knockbackY": knockback_y,
                "hitstunTicks": hitstun_ticks,
            },
        )

        debug_print(
            DEBUG_HIT,
            f"[SERVER PLAYER HIT] attacker={attacker.client_id} target={target.client_id} "
            f"weapon={weapon_id} damage={final_damage:.2f} "
            f"damageBefore={damage_before_hit:.2f} damageAfter={target.damage_percent:.2f} "
            f"growth={knockback_growth:.3f} weight={safe_weight:.2f} "
            f"baseKB={base_force:.2f} percentFactor={percentage_factor:.3f} "
            f"scale={KNOCKBACK_SCALE:.2f} finalForce={final_force:.2f} "
            f"kb=({knockback_x:.2f},{knockback_y:.2f}) "
            f"hitstunTicks={hitstun_ticks} until={target.hitstun_until_tick}"
        )

    # ------------------------------------------------------------------
    # Tick simulation
    # ------------------------------------------------------------------

    def step_projectiles(
        self,
        sessions: Dict[object, ClientSession],
        tick: int,
    ) -> None:
        dead_ids = set()

        for proj in list(self.projectiles.values()):
            if not proj.alive:
                dead_ids.add(proj.proj_id)
                continue

            # ------------------------------------------------------------
            # 1) TTL
            # ------------------------------------------------------------

            proj.timer += SIM_DT
            proj.ttl -= SIM_DT

            if proj.ttl <= 0.0:
                proj.alive = False
                dead_ids.add(proj.proj_id)

                self.push_event(
                    "PROJECTILE_DESTROYED",
                    {
                        "projId": proj.proj_id,
                        "reason": "ttl",
                        "x": proj.pos_x,
                        "y": proj.pos_y,
                    },
                )

                debug_print(
                    DEBUG_PROJECTILE,
                    f"[SERVER PROJECTILE TTL END] "
                    f"projId={proj.proj_id} "
                    f"weapon={proj.weapon_id} "
                    f"bulletId={getattr(proj, 'bullet_id', '')} "
                    f"pos=({proj.pos_x:.2f},{proj.pos_y:.2f})"
                )

                continue

            # ------------------------------------------------------------
            # 2) effects before move
            # 比如 hover_split 减速
            # ------------------------------------------------------------

            game_effects.apply_projectile_effects_before_move(
                self,
                sessions,
                proj,
            )

            if not proj.alive:
                dead_ids.add(proj.proj_id)
                continue

            # ------------------------------------------------------------
            # 3) move
            # ------------------------------------------------------------

            old_x = proj.pos_x
            old_y = proj.pos_y

            next_x = proj.pos_x + proj.vel_x * SIM_DT
            next_y = proj.pos_y + proj.vel_y * SIM_DT

            # ------------------------------------------------------------
            # 4) swept world collision
            # 子弹撞地图：只处理世界碰撞，不要 apply_hit。
            # ------------------------------------------------------------

            if self.projectile_swept_hits_world(
                old_x=old_x,
                old_y=old_y,
                next_x=next_x,
                next_y=next_y,
                radius=proj.radius,
            ):
                proj.pos_x = next_x
                proj.pos_y = next_y

                debug_print(
                    DEBUG_PROJECTILE,
                    f"[SERVER PROJECTILE WORLD HIT] "
                    f"projId={proj.proj_id} "
                    f"weapon={proj.weapon_id} "
                    f"bulletId={getattr(proj, 'bullet_id', '')} "
                    f"effects={proj.effect_ids} "
                    f"from=({old_x:.2f},{old_y:.2f}) "
                    f"to=({next_x:.2f},{next_y:.2f}) "
                    f"radius={proj.radius:.2f}"
                )

                handled = game_effects.apply_effects_on_projectile_world_hit(
                    self,
                    sessions,
                    proj,
                    tick,
                )

                # 如果 delayed_explosion 接管了 world hit，
                # game_effects 里已经会 push EXPLOSION_TRIGGERED 和 PROJECTILE_DESTROYED。
                if not handled:
                    proj.alive = False

                    self.push_event(
                        "PROJECTILE_DESTROYED",
                        {
                            "projId": proj.proj_id,
                            "reason": "world",
                            "x": proj.pos_x,
                            "y": proj.pos_y,
                        },
                    )
                else:
                    proj.alive = False

                dead_ids.add(proj.proj_id)
                continue

            # ------------------------------------------------------------
            # 5) swept player collision
            # ------------------------------------------------------------

            target = self.find_projectile_swept_hit_player(
                sessions=sessions,
                old_x=old_x,
                old_y=old_y,
                next_x=next_x,
                next_y=next_y,
                radius=proj.radius,
                owner_client_id=proj.owner_client_id,
            )

            if target is not None:
                attacker = self.find_session_by_client_id(
                    sessions,
                    proj.owner_client_id,
                )

                debug_print(
                    DEBUG_PROJECTILE,
                    f"[SERVER PROJECTILE PLAYER HIT] "
                    f"projId={proj.proj_id} "
                    f"owner={proj.owner_client_id} "
                    f"target={target.client_id} "
                    f"weapon={proj.weapon_id} "
                    f"bulletId={getattr(proj, 'bullet_id', '')} "
                    f"from=({old_x:.2f},{old_y:.2f}) "
                    f"to=({next_x:.2f},{next_y:.2f})"
                )

                handled = False

                if attacker is not None:
                    handled = game_effects.apply_effects_on_projectile_player_hit(
                        self,
                        sessions,
                        proj,
                        attacker,
                        target,
                        tick,
                    )

                # 如果 delayed_explosion 接管了 player hit，
                # game_effects.trigger_delayed_explosion 里已经会 apply_hit + push destroy。
                if not handled:
                    if attacker is not None:
                        self.apply_hit(
                            attacker=attacker,
                            target=target,
                            damage=proj.damage,
                            base_knockback=proj.base_knockback,
                            weapon_id=proj.weapon_id,
                            tick=tick,
                        )

                    proj.alive = False

                    self.push_event(
                        "PROJECTILE_DESTROYED",
                        {
                            "projId": proj.proj_id,
                            "reason": "hit_player",
                            "x": next_x,
                            "y": next_y,
                        },
                    )
                else:
                    proj.alive = False

                dead_ids.add(proj.proj_id)
                continue

            # ------------------------------------------------------------
            # 6) no collision, commit move
            # ------------------------------------------------------------

            proj.pos_x = next_x
            proj.pos_y = next_y

            if abs(proj.vel_x) > 0.0001 or abs(proj.vel_y) > 0.0001:
                proj.rotation_deg = math.degrees(
                    math.atan2(proj.vel_y, proj.vel_x)
                )

            # ------------------------------------------------------------
            # 7) effects after move
            # 比如 delayed_explosion 到时间爆炸 / hover_split 分裂
            # ------------------------------------------------------------

            game_effects.apply_projectile_effects_after_move(
                self,
                sessions,
                proj,
                tick,
            )

            if not proj.alive:
                dead_ids.add(proj.proj_id)

        # ------------------------------------------------------------
        # 8) cleanup
        # ------------------------------------------------------------

        for pid in dead_ids:
            if pid in self.projectiles:
                del self.projectiles[pid]

    def step_melee_hitboxes(
        self,
        sessions: Dict[object, ClientSession],
        tick: int,
    ) -> None:
        dead_ids = set()

        for hitbox in list(self.melee_hitboxes.values()):
            if not hitbox.alive:
                dead_ids.add(hitbox.hitbox_id)
                continue

            hitbox.timer += SIM_DT
            hitbox.ttl -= SIM_DT

            if hitbox.ttl <= 0.0:
                hitbox.alive = False
                dead_ids.add(hitbox.hitbox_id)
                continue

            attacker = self.find_session_by_client_id(
                sessions,
                hitbox.owner_client_id,
            )

            if attacker is None:
                hitbox.alive = False
                dead_ids.add(hitbox.hitbox_id)
                continue

            targets = self.find_players_in_radius(
                sessions=sessions,
                center_x=hitbox.center_x,
                center_y=hitbox.center_y,
                radius=hitbox.radius,
                ignore_client_id=hitbox.owner_client_id,
            )

            for target in targets:
                if target.client_id in hitbox.hit_targets:
                    continue

                self.apply_hit(
                    attacker=attacker,
                    target=target,
                    damage=hitbox.damage,
                    base_knockback=hitbox.base_knockback,
                    weapon_id=hitbox.weapon_id,
                    tick=tick,
                )

                if target.client_id is not None:
                    hitbox.hit_targets.add(target.client_id)

                if hitbox.hit_once:
                    hitbox.alive = False
                    dead_ids.add(hitbox.hitbox_id)
                    break

        for hid in dead_ids:
            if hid in self.melee_hitboxes:
                del self.melee_hitboxes[hid]