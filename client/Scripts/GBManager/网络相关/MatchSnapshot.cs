using System;

[Serializable]
public class MatchSnapshot
{
    public int tick;
    public int lastProcessedSeq;
    public string rejectReason;

    public PlayerSnapshot[] players;
    public ProjectileSnapshot[] projectiles;
    public MatchEventSnapshot[] events;
    public LootSnapshot[] loots;
    public PlayerSnapshot FindPlayer(string clientId)
    {
        if (players == null || string.IsNullOrWhiteSpace(clientId))
            return null;

        foreach (PlayerSnapshot p in players)
        {
            if (p != null && p.clientId == clientId)
                return p;
        }

        return null;
    }
}

[Serializable]
public class PlayerSnapshot
{
    public int slotNo;
    public int userId;
    public string clientId;

    public string state;
    public bool grounded;
    public int jumpCount;

    public float posX;
    public float posY;
    public float velX;
    public float velY;
    public float aimX;
    public float aimY;
    public float damagePercent;
    public int stocks;
    public bool isDead;
    public int facing;

    public float lastKnockbackX;
    public float lastKnockbackY;
    public int lastHitTick;
    public string equippedWeaponId;
    public string[] equippedEffectIds;
}

[Serializable]
public class ProjectileSnapshot
{
    public int projId;
    public string ownerClientId;
    public string weaponId;

    public string bulletId;
    public string visualId;

    public float posX;
    public float posY;
    public float velX;
    public float velY;
    public float rotationDeg;

    public float radius;
    public float ttl;
    public bool alive;

    public string[] effectIds;
}

[Serializable]
public class MatchEventSnapshot
{
    public string eventType;
    public int eventSeq;
    public MatchEventData data;
}

[Serializable]
public class MatchEventData
{
    public int projId;
    public int hitboxId;

    public string ownerClientId;
    public string attackerClientId;
    public string targetClientId;
    public string clientId;

    public string weaponId;
    public string bulletId;
    public string visualId;

    public float x;
    public float y;
    public float radius;

    public float velX;
    public float velY;
    public float rotationDeg;

    public float damageAdded;
    public float newDamagePercent;
    public float knockbackX;
    public float knockbackY;

    public int stocksLeft;


    public string lootId;
    public string lootType;
    public string itemId;

    public string reason;
}
[System.Serializable]
public class LootSnapshot
{
    public string lootId;
    public string lootType;
    public string itemId;

    public float posX;
    public float posY;
    public float radius;
    public float velY;
    public bool landed;
}