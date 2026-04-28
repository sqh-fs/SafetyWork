using System;
using UnityEngine;

public class NetworkEffectDatabase : MonoBehaviour
{
    public static NetworkEffectDatabase Instance { get; private set; }

    [Serializable]
    public class Entry
    {
        public string effectId;
        public WeaponEffectSO effect;
    }

    [SerializeField] private Entry[] entries;

    private void Awake()
    {
        Instance = this;
    }

    public WeaponEffectSO GetEffect(string effectId)
    {
        if (string.IsNullOrWhiteSpace(effectId))
            return null;

        if (entries == null)
            return null;

        foreach (Entry entry in entries)
        {
            if (entry == null)
                continue;

            if (entry.effectId == effectId)
                return entry.effect;
        }

        Debug.LogWarning($"[NetworkEffectDatabase] ’“≤ªµΩ effectId={effectId}");
        return null;
    }
}