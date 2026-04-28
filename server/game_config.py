HOST = "0.0.0.0"
PORT = 8765
# relay_server 相关
DEBUG_INPUT = False
DEBUG_ATTACK = False

DEBUG_LOOT = False
DEBUG_ROOM = True
DEBUG_CONNECTION = True

# game_combat 相关
DEBUG_COMBAT_WARN = False
DEBUG_ATTACK = False
DEBUG_PROJECTILE = False
DEBUG_HIT = False

# -----------------------------
# Snapshot broadcast throttle
# -----------------------------
# True  = 按 SNAPSHOT_INTERVAL_TICKS 降频广播
# False = 每 tick 都广播，恢复旧逻辑
SNAPSHOT_THROTTLE_ENABLED = True

# 每多少个 server tick 广播一次 snapshot。
# 1 = 每 tick 广播
# 2 = 每 2 tick 广播
# 3 = 每 3 tick 广播
SNAPSHOT_INTERVAL_TICKS = 2

# 是否遇到事件时强制立即广播。
# 测带宽时建议 False。
# 如果你发现命中/拾取/爆炸事件延迟明显，可以改 True。
SNAPSHOT_FORCE_BROADCAST_ON_EVENTS = False


MAX_PROJECTILES = 50

MAX_JUMP_COUNT = 2
MOVEMENT_MULTIPLIER = 0.5

# -----------------------------
# Simplified map / collision
# -----------------------------
GROUND_EPSILON = 0.001
PLAYER_HALF_WIDTH = 0.46
PLAYER_HALF_HEIGHT = 0.42

# -----------------------------
# Unified movement parameters
# -----------------------------
SIM_DT = SIM_DT = 1.0 / 30.0
MOVE_SPEED = 32.0 * MOVEMENT_MULTIPLIER

GRAVITY = -2.0 * MOVEMENT_MULTIPLIER
JUMP_VELOCITY = 30.0 * MOVEMENT_MULTIPLIER
FALL_SPEED_CAP = -36.0 * MOVEMENT_MULTIPLIER

OFFSET_Y = 0.7
GROUND_Y = -1.45 + OFFSET_Y




RESPAWN_DELAY_SECONDS = 2.0
RESPAWN_DELAY_TICKS = int(RESPAWN_DELAY_SECONDS / SIM_DT)
# -----------------------------
# Smash-like combat defaults
# -----------------------------
KNOCKBACK_SCALE = 0.55
HITSTUN_BASE_TICKS = 12
HITSTUN_PERCENT_FACTOR_TO_TICKS = 0.12
KNOCKBACK_DRAG_X = 0.86

DEFAULT_WEIGHT = 25.0
DEFAULT_KNOCKBACK_GROWTH = 0.2
DEFAULT_BASE_KNOCKBACK = 25.0

# -----------------------------
# Blast zone
# -----------------------------
BLAST_X_MIN = -12.0
BLAST_X_MAX = 32.0
BLAST_Y_MIN = -6.0
BLAST_Y_MAX = 12.0

# -----------------------------
# Game Server message types
# -----------------------------
TYPE_JOIN_ROOM = "JOIN_ROOM"
TYPE_INPUT = "INPUT"
TYPE_CHAT = "CHAT"
TYPE_LEAVE_ROOM = "LEAVE_ROOM"
TYPE_SNAPSHOT = "SNAPSHOT"
TYPE_ERROR = "ERROR"
TYPE_SERVER_BROADCAST = "SERVER_BROADCAST"
TYPE_CREATE_ROOM = "CREATE_ROOM"
TYPE_READY = "READY"
TYPE_START_GAME = "START_GAME"
TYPE_ROOM_STATE = "ROOM_STATE"
TYPE_GAME_START = "GAME_START"
GS_JSON_PAYLOAD_TYPES = {
    TYPE_INPUT,
    TYPE_CHAT,
    TYPE_CREATE_ROOM,
    TYPE_READY,
    TYPE_START_GAME,
}
LOOT_SPAWN_INTERVAL_TICKS = int(2.0 / SIM_DT)
LOOT_PICKUP_RADIUS = 0.75
# -----------------------------
# Loot falling
# -----------------------------
LOOT_MAX_ALIVE = 5

# 空投生成时从目标区域上方多高开始掉落
LOOT_SPAWN_Y = 8.5

# 空投下落重力，数值可以比角色重力大一点
LOOT_GRAVITY = -0.04

# 最大下落速度
LOOT_FALL_SPEED_CAP = -2.0

# 空投中心落在平台上方多少
# 你的 loot sprite 如果中心在箱子中心，这里一般 0.45~0.7
LOOT_HALF_HEIGHT = 0.4

# 平台边缘安全距离，避免刚好落到边缘
LOOT_DROP_PLATFORM_MARGIN = 0.33

# 是否只有落地后才能捡
LOOT_PICKUP_ONLY_WHEN_LANDED = False
EFFECT_DROP_POINTS = [
    {"x": 3.0, "y": GROUND_Y + 0.6},
    {"x": 10.0, "y": GROUND_Y + 0.6},
    {"x": 17.0, "y": GROUND_Y + 0.6},
]
EFFECT_DROP_POOL = [
    "delayed_explosion",
    "hover_split",
    "parry",
    "sword_wave",
]
WEAPON_DROP_POOL = [
    "手枪",
    "重机枪",
    "狙击枪",
    "霰弹枪",
    "短剑",
]

LOOT_TYPE_WEIGHTS = {
    "effect": 0.5,
    "weapon": 0.5,
}
# -----------------------------
# Spawn / Respawn points
# 注意：这里是服务器逻辑坐标，pos_y 是 footY，不是 Unity transform centerY
# -----------------------------
SPAWN_POINTS = {
    "Client1": {
        "x": 2.0,
        "y": 5.0,
    },
    "Client2": {
        "x": 18.0,
        "y": 5.0,
    },
}

RESPAWN_POINTS = {
    "Client1": {
        "x": 2.0,
        "y": 5.0,
    },
    "Client2": {
        "x": 18.0,
        "y": 5.0,
    },
}
# -----------------------------
# Weapon runtime config
# attack_mode: "ranged" | "melee"
# -----------------------------
WEAPON_DB = {
    "手枪": {
        "attack_mode": "ranged",
        "bullet_id": "普通子弹",
        "fire_interval_ticks": 15,
        "auto_fire": True,
    },

    "狙击枪": {
        "attack_mode": "ranged",
        "bullet_id": "狙击子弹",
        "fire_interval_ticks": 55,
        "auto_fire": True,
    },

    "重机枪": {
        "attack_mode": "ranged",
        "bullet_id": "机枪子弹",
        "fire_interval_ticks": 6,
        "auto_fire": True,
    },

    "霰弹枪": {
        "attack_mode": "ranged",
        "bullet_id": "霰弹",
        "pellet_count": 5,
        "spread_angle_deg": 25.0,
        "fire_interval_ticks": 25,
        "auto_fire": True,
    },

    "短剑": {
        "attack_mode": "melee",
        "melee_profile": "短剑",
        "fire_interval_ticks": 12,
        "auto_fire": False,
    },
}

# -----------------------------
# Melee hitbox config
# 第一版：近战用“前方圆形 hitbox + 短存活”
# -----------------------------
MELEE_DB = {
    "短剑": {
        "startup_time": 0.00,
        "active_time": 0.10,
        "radius": 0.90,
        "offset_x": 0.85,
        "offset_y": 0.40,
        "base_damage": 10.0,
        "base_knockback": 2.2,
        "hit_once": True,
    }
}

BULLET_DB = {
    "普通子弹": {
        "speed": 15.0,
        "radius": 0.20,
        "ttl": 2.0,
        "visual_id": "普通子弹",
        "base_damage": 12.0,
        "base_knockback": 2.0,
    },

    "狙击子弹": {
        "speed": 25.0,
        "radius": 0.16,
        "ttl": 2,
        "visual_id": "狙击子弹",
        "base_damage": 22.0,
        "base_knockback": 3.2,
    },

    "机枪子弹": {
        "speed": 15.0,
        "radius": 0.18,
        "ttl": 1.6,
        "visual_id": "机枪子弹",
        "base_damage": 6.0,
        "base_knockback": 1.0,
    },

    "剑气": {
        "speed": 15.0,
        "radius": 0.28,
        "ttl": 1.2,
        "visual_id": "剑气",
        "base_damage": 8.0,
        "base_knockback": 1.8,
    },
    "霰弹": {
    "speed": 20.0,
    "radius": 0.13,
    "ttl": 0.75,
    "visual_id": "霰弹",
    "base_damage": 5.0,
    "base_knockback": 0.9,
    },
}
# -----------------------------
# Effect runtime config
# -----------------------------
EFFECT_DB = {
    "delayed_explosion": {
        "hook": "on_projectile_spawned",
        "delay_time": 2.0,
        "explosion_radius": 2.5,
        "damage_multiplier": 1.5,
        "explode_on_world_hit": True,
        "explode_on_player_hit": True,
    },
    "hover_split": {
        "hook": "on_projectile_spawned",
        "slow_duration": 0.5,
        "split_count": 3,
        "split_mode": "radial",
        "inherit_effects_except_self": True,
    },
    "parry": {
        "hook": "on_attack_execute",
        "parry_window": 0.2,
        "deflect_speed_multiplier": 1.5,
        "hitstop_duration": 0.15,
    },
    "sword_wave": {
        "hook": "on_attack_execute",
        "spawn_projectile": True,
        "projectile_kind": "sword_wave",
        "speed": 14.0,
        "damage_multiplier": 0.8,
        "inherit_runtime_effects": True,
    },

    
}