using UnityEngine;

public class ClientReceiver : MonoBehaviour
{
    public static ClientReceiver Instance;

    [Header("场景玩家")]
    [SerializeField] private Player player1;
    [SerializeField] private Player player2;

    [Header("本地预测")]
    [SerializeField] private ClientPredictionController predictionController;

    [Header("调试")]
    [SerializeField] private bool debugSnapshotLog = true;
    [SerializeField] private bool debugEventLog = true;

    private string localClientId = "";
    [SerializeField] private float playerHalfHeight = 0.42f;
    private int lastP1Stocks = -999;
    private int lastP2Stocks = -999;
    private void Awake()
    {
        Instance = this;
    }

    public void SetLocalClientId(string id)
    {
        localClientId = id;
        Debug.Log($"[ClientReceiver] localClientId = {localClientId}");
    }

    public void BindPlayers(
        Player p1,
        Player p2,
        ClientPredictionController prediction,
        string clientId
    )
    {
        player1 = p1;
        player2 = p2;
        predictionController = prediction;

        SetLocalClientId(clientId);

        Debug.Log(
            $"[ClientReceiver] BindPlayers " +
            $"p1={(player1 != null ? player1.name : "null")} " +
            $"p2={(player2 != null ? player2.name : "null")} " +
            $"local={localClientId}"
        );
    }
    private void ApplyStocksFromSnapshot(MatchSnapshot snapshot)
    {
        if (snapshot == null || snapshot.players == null)
            return;

        int p1Stocks = lastP1Stocks;
        int p2Stocks = lastP2Stocks;

        foreach (PlayerSnapshot ps in snapshot.players)
        {
            if (ps == null)
                continue;

            if (ps.clientId == "Client1")
                p1Stocks = ps.stocks;
            else if (ps.clientId == "Client2")
                p2Stocks = ps.stocks;
        }

        if (p1Stocks == lastP1Stocks && p2Stocks == lastP2Stocks)
            return;

        lastP1Stocks = p1Stocks;
        lastP2Stocks = p2Stocks;

        UIManager.Instance?.UpdateStocks(p1Stocks, p2Stocks);
    }
    public void OnReceiveSnapshot(MatchSnapshot snapshot)
    {
        if (snapshot == null)
            return;

        if (debugSnapshotLog)
        {
            Debug.Log(
                $"[ClientReceiver] SNAPSHOT tick={snapshot.tick} ack={snapshot.lastProcessedSeq} " +
                $"players={(snapshot.players != null ? snapshot.players.Length : 0)} " +
                $"projectiles={(snapshot.projectiles != null ? snapshot.projectiles.Length : 0)} " +
                $"events={(snapshot.events != null ? snapshot.events.Length : 0)} " +
                $"local={localClientId} reject={snapshot.rejectReason}"
            );
        }

        ApplyPlayers(snapshot);
        ApplyProjectiles(snapshot);
        ApplyStocksFromSnapshot(snapshot);
        ApplyLoots(snapshot);
        ConsumeEvents(snapshot);
    }
    private void ApplyLoots(MatchSnapshot snapshot)
    {
        if (LootViewManager.Instance == null)
            return;

        LootViewManager.Instance.ApplySnapshot(snapshot.loots);
    }
    private void ApplyPlayers(MatchSnapshot snapshot)
    {
        if (snapshot.players == null)
            return;

        foreach (PlayerSnapshot ps in snapshot.players)
        {
            if (ps == null || string.IsNullOrWhiteSpace(ps.clientId))
                continue;

            Player target = GetPlayerByClientId(ps.clientId);

            if (target == null)
            {
                if (debugSnapshotLog)
                    Debug.LogWarning($"[ClientReceiver] 找不到 clientId={ps.clientId} 对应的 Player");

                continue;
            }

            bool isLocal = ps.clientId == localClientId;

            if (isLocal)
            {
                predictionController?.Reconcile(snapshot, localClientId);

           
            }
            else
            {
                ApplyRemotePlayerSnapshot(target, ps);
               
            }

            ApplyCommonPlayerState(target, ps);

            if (debugSnapshotLog)
            {
                Debug.Log(
                    $"[ClientReceiver] player={ps.clientId} local={isLocal} " +
                    $"target={target.name} pos=({ps.posX:F2},{ps.posY:F2}) " +
                    $"state={ps.state} grounded={ps.grounded} " +
                    $"damage={ps.damagePercent:F1} stocks={ps.stocks}"
                );
            }
        }
    }

    private void ApplyRemotePlayerSnapshot(Player target, PlayerSnapshot ps)
    {
        float displayY = ps.posY + playerHalfHeight;

        target.ApplyServerPosition(
            ps.posX,
            displayY,
            ps.velY
        );

        target.ApplyServerState(
            ps.state,
            ps.grounded,
            ps.jumpCount
        );
        //target.ApplyServerWeapon(ps.equippedWeaponId);
        target.ApplyNetworkAim(ps.aimX, ps.aimY);
    }
    private void ApplyCommonPlayerState(Player target, PlayerSnapshot ps)
    {
        if (target == null || ps == null)
            return;

        target.ApplyServerWeapon(ps.equippedWeaponId);
        target.ApplyServerEffects(ps.equippedEffectIds);
        Player_Health health = target.GetComponent<Player_Health>();

        if (health != null)
        {
            health.ApplyServerPlayerHealthSnapshot(ps);
        }
    }
    private void ApplyProjectiles(MatchSnapshot snapshot)
    {
        if (ProjectileViewManager.Instance == null)
            return;

        ProjectileViewManager.Instance.ApplySnapshot(snapshot.projectiles);
    }

    private void ConsumeEvents(MatchSnapshot snapshot)
    {
        if (snapshot.events == null)
            return;

        foreach (MatchEventSnapshot evt in snapshot.events)
        {
            if (evt == null)
                continue;

            MatchEventData data = evt.data;

            if (debugEventLog)
            {
                Debug.Log(
                    $"[ClientReceiver] EVENT {evt.eventType} seq={evt.eventSeq}"
                );
            }

            switch (evt.eventType)
            {
                case "PROJECTILE_SPAWNED":
                    {
                        HandleProjectileSpawned(evt, data);
                        break;
                    }

                case "PROJECTILE_DESTROYED":
                    {
                        HandleProjectileDestroyed(evt, data);
                        break;
                    }

                case "PLAYER_HIT":
                    {
                        HandlePlayerHit(evt, data);
                        break;
                    }

                case "EXPLOSION_TRIGGERED":
                    {
                        HandleExplosionTriggered(evt, data);
                        break;
                    }

                case "MELEE_HITBOX_SPAWNED":
                    {
                        Debug.Log(
                            $"[ClientReceiver] EVENT MELEE_HITBOX_SPAWNED seq={evt.eventSeq} " +
                            $"owner={evt.data.ownerClientId} weapon={evt.data.weaponId}"
                        );

                        Player attacker = GetPlayerByClientId(evt.data.ownerClientId);

                        if (attacker != null)
                        {
                            attacker.ApplyServerWeapon(evt.data.weaponId);
                            attacker.PlayNetworkMeleeAttack();
                        }

                        break;
                    }

                case "PLAYER_PARRIED":
                    {
                        HandlePlayerParried(evt, data);
                        break;
                    }

                case "PLAYER_OUT_OF_BOUNDS":
                    {
                        HandlePlayerOutOfBounds(evt, data);
                        break;
                    }

                case "PLAYER_RESPAWN":
                    {
                        HandlePlayerRespawn(evt, data);
                        break;
                    }
                case "LOOT_SPAWNED":
                    {
                        HandleLootSpawned(evt, data);
                        break;
                    }

                case "LOOT_PICKED":
                    {
                        HandleLootPicked(evt, data);
                        break;
                    }

                case "LOOT_DESPAWNED":
                    {
                        HandleLootDespawned(evt, data);
                        break;
                    }

                default:
                    {
                        if (debugEventLog)
                            Debug.Log($"[ClientReceiver] 未处理事件 type={evt.eventType}");

                        break;
                    }
            }
        }
    }
    private Player FindPlayerByClientId(string clientId)
{
    if (string.IsNullOrWhiteSpace(clientId))
        return null;

    Player[] players = FindObjectsByType<Player>(FindObjectsSortMode.None);

    foreach (Player p in players)
    {
        if (p != null && p.ClientId == clientId)
            return p;
    }

    return null;
}
    private void HandleProjectileSpawned(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] PROJECTILE_SPAWNED " +
                $"projId={data.projId} owner={data.ownerClientId} " +
                $"weapon={data.weaponId} bullet={data.bulletId} visual={data.visualId} " +
                $"pos=({data.x:F2},{data.y:F2}) rot={data.rotationDeg:F1}"
            );
        }

        // 注意：Projectile 的显示通常由 ProjectileViewManager.ApplySnapshot(snapshot.projectiles) 负责。
        // 这里一般不需要 Instantiate，否则可能重复生成。
        //
        // 这个事件更适合播枪口火花、后坐动画、开火音效。
        Player owner = GetPlayerByClientId(data.ownerClientId);

        if (owner != null)
        {
            // 如果你后面给 Player 加了网络开火反馈，可以打开：
            // owner.PlayNetworkFireFeedback(data.weaponId, data.bulletId);
        }
    }

    private void HandleProjectileDestroyed(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] PROJECTILE_DESTROYED " +
                $"projId={data.projId} reason={data.reason} " +
                $"pos=({data.x:F2},{data.y:F2})"
            );
        }

        // 如果你的 NetworkVFXManager 里有 SpawnProjectileDestroy，可以打开。
        // NetworkVFXManager.Instance?.SpawnProjectileDestroy(data.x, data.y);
    }

    private void HandlePlayerHit(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] PLAYER_HIT " +
                $"attacker={data.attackerClientId} target={data.targetClientId} " +
                $"damage+={data.damageAdded:F1} newDamage={data.newDamagePercent:F1} " +
                $"kb=({data.knockbackX:F2},{data.knockbackY:F2})"
            );
        }

        Player target = GetPlayerByClientId(data.targetClientId);

        if (target != null)
        {
            Vector3 pos = target.transform.position + Vector3.up * 0.5f;
            NetworkVFXManager.Instance?.SpawnHit(pos.x, pos.y);
        }
    }

    private void HandleExplosionTriggered(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] EXPLOSION_TRIGGERED " +
                $"projId={data.projId} pos=({data.x:F2},{data.y:F2}) radius={data.radius:F2}"
            );
        }

        NetworkVFXManager.Instance?.SpawnExplosion(
            data.x,
            data.y,
            data.radius
        );
    }

    private void HandleMeleeHitboxSpawned(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] MELEE_HITBOX_SPAWNED " +
                $"hitboxId={data.hitboxId} owner={data.ownerClientId} " +
                $"weapon={data.weaponId} pos=({data.x:F2},{data.y:F2}) radius={data.radius:F2}"
            );
        }

        NetworkVFXManager.Instance?.SpawnMelee(
            data.x,
            data.y,
            data.radius
        );
    }

    private void HandlePlayerParried(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] PLAYER_PARRIED " +
                $"client={data.clientId} projId={data.projId}"
            );
        }

        Player p = GetPlayerByClientId(data.clientId);

        if (p != null)
        {
            Vector3 pos = p.transform.position + Vector3.up * 0.5f;
            NetworkVFXManager.Instance?.SpawnParry(pos.x, pos.y);
        }
    }

    private void HandlePlayerOutOfBounds(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] PLAYER_OUT_OF_BOUNDS " +
                $"client={data.clientId} stocksLeft={data.stocksLeft}"
            );
        }

        Player p = GetPlayerByClientId(data.clientId);

        if (p != null)
        {
       
             NetworkVFXManager.Instance?.Spawn("death", p.transform.position.x, p.transform.position.y);
        }
    }

    private void HandlePlayerRespawn(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] PLAYER_RESPAWN " +
                $"client={data.clientId} pos=({data.x:F2},{data.y:F2})"
            );
        }

        Player p = GetPlayerByClientId(data.clientId);

        if (p != null)
        {
            p.ApplyServerPosition(
                data.x,
                  data.y + playerHalfHeight,
                0f
            );

            p.ApplyServerState(
                "Grounded",
                true,
                0
            );
        }
    }

    private Player GetPlayerByClientId(string clientId)
    {
        if (clientId == "Client1")
            return player1;

        if (clientId == "Client2")
            return player2;

        return null;
    }

    private void HandleLootSpawned(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] LOOT_SPAWNED " +
                $"lootId={data.lootId} type={data.lootType} item={data.itemId} " +
                $"pos=({data.x:F2},{data.y:F2})"
            );
        }

        // 其实 snapshot.loots 已经会生成，这里主要可以播出现特效。
        //NetworkVFXManager.Instance?.Spawn("loot_spawn", data.x, data.y);
    }

    private void HandleLootPicked(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        if (debugEventLog)
        {
            Debug.Log(
                $"[ClientReceiver] LOOT_PICKED " +
                $"lootId={data.lootId} item={data.itemId} by={data.clientId}"
            );
        }

        LootViewManager.Instance?.Remove(data.lootId);
        //NetworkVFXManager.Instance?.Spawn("loot_pick", data.x, data.y);
    }

    private void HandleLootDespawned(MatchEventSnapshot evt, MatchEventData data)
    {
        if (data == null)
            return;

        LootViewManager.Instance?.Remove(data.lootId);
    }
}