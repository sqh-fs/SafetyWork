using UnityEngine;

public class LootPickupView : MonoBehaviour
{
    [Header("显示")]
    [SerializeField] private SpriteRenderer spriteRenderer;

    [Header("调试信息")]
    [SerializeField] private string lootId;
    [SerializeField] private string lootType;
    [SerializeField] private string itemId;

    private void Awake()
    {
        if (spriteRenderer == null)
            spriteRenderer = GetComponentInChildren<SpriteRenderer>();
    }

    public void Init(string newLootId, string newLootType, string newItemId, Sprite icon)
    {
        lootId = newLootId;
        lootType = newLootType;
        itemId = newItemId;

        if (spriteRenderer == null)
            spriteRenderer = GetComponentInChildren<SpriteRenderer>();

        if (spriteRenderer != null && icon != null)
            spriteRenderer.sprite = icon;

        gameObject.name = $"LootView_{lootId}_{lootType}_{itemId}";
    }
}