using System.Collections.Generic;
using UnityEngine;
using DG.Tweening;
using Unity.VisualScripting;

public class LootViewManager : MonoBehaviour
{
    public static LootViewManager Instance { get; private set; }

    [Header("Prefab")]
    [SerializeField] private GameObject lootPickupViewPrefab;

    [Header("数据库")]
    [SerializeField] private NetworkWeaponDatabase weaponDatabase;
    [SerializeField] private NetworkEffectDatabase effectDatabase;

    [Header("坐标")]
    [SerializeField] private float lootHalfHeight = 0f;

    [Header("下落表现")]
    [SerializeField] private bool playDropAnimation = true;
    [SerializeField] private float dropFromHeight = 6f;
    [SerializeField] private float dropDuration = 0.65f;

    private readonly Dictionary<string, GameObject> activeLoots = new();
    private readonly HashSet<string> droppingLoots = new();

    private void Awake()
    {
        Instance = this;
    }

    public void ApplySnapshot(LootSnapshot[] loots)
    {
        HashSet<string> aliveIds = new HashSet<string>();

        if (loots != null)
        {
            foreach (LootSnapshot loot in loots)
            {
                if (loot == null || string.IsNullOrWhiteSpace(loot.lootId))
                    continue;

                aliveIds.Add(loot.lootId);
                SpawnOrUpdate(loot);
            }
        }

        List<string> toRemove = new List<string>();

        foreach (var pair in activeLoots)
        {
            if (!aliveIds.Contains(pair.Key))
                toRemove.Add(pair.Key);
        }

        foreach (string lootId in toRemove)
            Remove(lootId);
    }

    public void SpawnOrUpdate(LootSnapshot loot)
    {
        if (loot == null)
            return;

        Vector3 targetPos = new Vector3(
            loot.posX,
            loot.posY + lootHalfHeight,
            0f
        );

        if (activeLoots.TryGetValue(loot.lootId, out GameObject existing))
        {
            if (existing != null && !droppingLoots.Contains(loot.lootId))
                existing.transform.position = targetPos;

            return;
        }

        if (lootPickupViewPrefab == null)
        {
            Debug.LogWarning("[LootViewManager] lootPickupViewPrefab 没有拖");
            return;
        }

        Vector3 spawnPos = playDropAnimation
            ? targetPos + Vector3.up * dropFromHeight
            : targetPos;

        GameObject go = Instantiate(lootPickupViewPrefab, spawnPos, Quaternion.identity);
        activeLoots[loot.lootId] = go;

        Sprite icon = GetIconForLoot(loot);

        LootPickupView view = go.GetComponent<LootPickupView>();

        if (view != null)
        {
            view.Init(
                loot.lootId,
                loot.lootType,
                loot.itemId,
                icon
            );
        }
        else
        {
            Debug.LogWarning("[LootViewManager] prefab 上没有 LootPickupView");
        }

        if (playDropAnimation)
        {
            droppingLoots.Add(loot.lootId);

            go.transform
                .DOMove(targetPos, dropDuration)
                .SetEase(Ease.OutBounce)
                .OnComplete(() =>
                {
                    droppingLoots.Remove(loot.lootId);

                    if (go != null)
                        go.transform.position = targetPos;
                });
        }
    }

    public void Remove(string lootId)
    {
        if (string.IsNullOrWhiteSpace(lootId))
            return;

        droppingLoots.Remove(lootId);

        if (!activeLoots.TryGetValue(lootId, out GameObject go))
            return;

        activeLoots.Remove(lootId);

        if (go != null)
        {
            go.transform.DOKill();
            Destroy(go);
        }
    }

    private Sprite GetIconForLoot(LootSnapshot loot)
    {
        if (loot == null)
            return null;

        if (loot.lootType == "weapon")
        {
            WeaponDataSO weaponData = null;

            if (weaponDatabase != null)
                weaponData = weaponDatabase.GetWeaponData(loot.itemId);
            else if (NetworkWeaponDatabase.Instance != null)
                weaponData = NetworkWeaponDatabase.Instance.GetWeaponData(loot.itemId);

            if (weaponData == null)
            {
                Debug.LogWarning($"[LootViewManager] 找不到 weaponId={loot.itemId}");
                return null;
            }

            return weaponData.icon;
        }

        if (loot.lootType == "effect")
        {
            WeaponEffectSO effect = null;

            if (effectDatabase != null)
                effect = effectDatabase.GetEffect(loot.itemId);
            else if (NetworkEffectDatabase.Instance != null)
                effect = NetworkEffectDatabase.Instance.GetEffect(loot.itemId);

            if (effect == null)
            {
                Debug.LogWarning($"[LootViewManager] 找不到 effectId={loot.itemId}");
                return null;
            }

            return effect.buffIcon;
        }

        return null;
    }
}