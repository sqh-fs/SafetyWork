using System;
using System.Collections;
using System.Globalization;
using PixelCrushers.DialogueSystem;
using Unity.VisualScripting;
using Unity.VisualScripting.Antlr3.Runtime;
using UnityEngine;
using UnityEngine.InputSystem;

public class Player : Entity
{
    //public Player_VFX vfx { get; private set; }
    public static Player instance;
    public Entity_Health health { get; private set; }
    public Entity_StatusHandler statusHandler { get; private set; }
    public Player_Combat combat { get; private set; }

    public Inventory_Player inventory { get; private set; }
    public PlayerInputSet input { get; private set; }

    public static event Action OnPlayerDeath;
    public Player_IdleState idleState { get; private set; }
    public Player_MoveState moveState { get; private set; }
    public Player_JumpState jumpState { get; private set; }
    public Player_FallState fallState { get; private set; }
    public Player_WallJumpState wallJumpState { get; private set; }
    public Player_WallSlideState wallSlideState { get; private set; }
    public Player_DeadState deadState { get; private set; }
    public Player_DashState dashState { get; private set; }
    public Player_BasicAttackState basicAttackState { get; private set; }
    public Player_JumpAttackState jumpAttackState { get; private set; }
    public Player_CastingState castingState { get; private set; }
    public ProximitySelector selector { get; private set; }
    public Player_QuestManager questManager { get; private set; }
    public Player_Exp playerExp { get; private set; }

    public Vector2 cachedMoveInput { get; private set; }
    public bool jumpPressedThisFrame { get; private set; }
    public bool dashPressedThisFrame { get; private set; }
    public bool attackPressedThisFrame { get; private set; }
    public bool attackHeld { get; private set; }
    public UI ui { get; private set; }

    [Header("Movement Details")]
    public float moveSpeed = 1.0f;
    public float jumpForce = 15f;
    public Vector2 wallJumpForce;
    public float dashDuration = .3f;
    public float dashSpeed = 25f;
    [Header("Jump Rules")]
    public int maxJumpCount = 2;   // 2 = 支持二段跳
    [SerializeField] private int currentJumpCount = 0;
    public Vector2 moveInput { get; private set; }
    public Vector2 mousePosition { get; private set; }

    [Range(0, 1)]
    public float inAirMoveMultiplier = .7f;

    [Range(0, 1)]
    public float wallSlideSlowMultiplier = .7f;

    [Header("Attack Details")]
    public Vector2[] attackVelocity;
    public Vector2 JumpAttackVelocity;

    public float attackVelocityDuration = .1f;
    public float comboResetTime = 1;
    private Coroutine queuedAttackCo;

    [Header("是否网络测试Control")]
    public bool useNetworkControl = false;

    private PlayerCombatController weaponController;

    protected override void Awake()
    {
        base.Awake();
        instance = this;
        input = new PlayerInputSet();
        stats = GetComponent<Player_Stats>();
        //vfx = GetComponent<Player_VFX>();
        ui = FindAnyObjectByType<UI>();
        statusHandler = GetComponent<Entity_StatusHandler>();
        health = GetComponent<Entity_Health>();
        combat = GetComponent<Player_Combat>();
        //ui.SetupControlsUI(input);
        inventory = GetComponent<Inventory_Player>();
        selector = GetComponent<ProximitySelector>();
        questManager = GetComponent<Player_QuestManager>();
        playerExp = GetComponent<Player_Exp>();
        weaponController = GetComponent<PlayerCombatController>();

        idleState = new Player_IdleState(this, stateMachine, "idle");
        moveState = new Player_MoveState(this, stateMachine, "move");
        jumpState = new Player_JumpState(this, stateMachine, "jumpfall");
        fallState = new Player_FallState(this, stateMachine, "jumpfall");
        wallJumpState = new Player_WallJumpState(this, stateMachine, "jumpfall");
        wallSlideState = new Player_WallSlideState(this, stateMachine, "wallSlide");
        dashState = new Player_DashState(this, stateMachine, "dash");
        deadState = new Player_DeadState(this, stateMachine, "dead");
        basicAttackState = new Player_BasicAttackState(this, stateMachine, "basicAttack");
        jumpAttackState = new Player_JumpAttackState(this, stateMachine, "jumpAttack");
        castingState = new Player_CastingState(this, stateMachine, "casting");
    }

    private void SetLayerRecursively(GameObject obj, int newLayer)
    {
        obj.layer = newLayer;
        foreach (Transform child in obj.transform)
        {
            SetLayerRecursively(child.gameObject, newLayer);
        }
    }

    public void OnPlayerSpawned(int playerID)
    {
        int targetLayer;

        if (playerID == 1)
        {
            targetLayer = LayerMask.NameToLayer("Player1");
        }
        else
        {
            targetLayer = LayerMask.NameToLayer("Player2");
        }

        SetLayerRecursively(gameObject, targetLayer);
    }

    protected override void Start()
    {
        base.Start();
        stateMachine.Initialize(idleState);

        if (currentWeaponData != null)
            EquipWeapon(currentWeaponData);
    }

    public void EquipWeapon(WeaponDataSO newWeaponData)
    {
        if (currentWeaponData != null)
        {
            ((Entity_Stats)stats).RemoveWeaponModifiers(currentWeaponData);
        }

        currentWeaponData = newWeaponData;

        if (currentWeaponInstance != null)
        {
            Destroy(currentWeaponInstance.gameObject);
        }

        Transform targetHoldPoint = newWeaponData.isRanged ? gunHoldPoint : bladeHoldePoint;

        if (targetHoldPoint == null)
        {
            Debug.LogError($"[Player] 警告：没有找到 {(newWeaponData.isRanged ? "Gun" : "Blade")} Hold Point！");
            return;
        }

        GameObject weaponObj = Instantiate(newWeaponData.weaponPrefab, targetHoldPoint);
        weaponObj.transform.localPosition = Vector3.zero;
        weaponObj.transform.localRotation = Quaternion.identity;

        currentWeaponInstance = weaponObj.GetComponent<Weapon>();
        if (currentWeaponInstance != null)
        {
            currentWeaponInstance.SetupWeapon(newWeaponData, this);
            weaponController.EquipWeapon(currentWeaponInstance);
        }

        if (currentWeaponData != null)
        {
            ((Entity_Stats)stats).ApplyWeaponModifiers(currentWeaponData);
        }
    }

    private void AimTowardsMouse()
    {
        if (currentWeaponData == null || Mouse.current == null)
            return;

        Vector2 mouseScreenPosition = Mouse.current.position.ReadValue();
        Vector3 mousePosition = Camera.main.ScreenToWorldPoint(mouseScreenPosition);
        mousePosition.z = 0f;

        Transform activeHoldPoint = currentWeaponData.isRanged ? gunHoldPoint : bladeHoldePoint;
        Transform idleHoldPoint = currentWeaponData.isRanged ? bladeHoldePoint : gunHoldPoint;

        if (activeHoldPoint != null)
        {
            Vector3 aimDirection = mousePosition - activeHoldPoint.position;

            float localX = aimDirection.x * facingDir;
            float localY = aimDirection.y;

            float angle = Mathf.Atan2(localY, localX) * Mathf.Rad2Deg;
            activeHoldPoint.localRotation = Quaternion.Euler(0, 0, angle);
        }

        if (idleHoldPoint != null)
        {
            idleHoldPoint.localRotation = Quaternion.identity;
        }
    }

    public override void HandleFlip(float xVelocity)
    {
        // 留空即可
    }

    private void FlipCharacterTowardsMouse()
    {
        if (Mouse.current == null)
            return;

        Vector2 mouseScreenPosition = Mouse.current.position.ReadValue();
        Vector3 mousePosition = Camera.main.ScreenToWorldPoint(mouseScreenPosition);

        if (mousePosition.x > transform.position.x && !facingRight)
        {
            Flip();
        }
        else if (mousePosition.x < transform.position.x && facingRight)
        {
            Flip();
        }
    }

    protected override IEnumerator SpeedUpEntityCo(float duration, float accMultiplier)
    {
        Debug.Log("Speed up!" + accMultiplier);

        float originalMoveSpeed = moveSpeed;
        float originalJumpForce = jumpForce;
        float originalAnimSpeed = anim.speed;
        Vector2 originalWallJump = wallJumpForce;
        Vector2 originalJumpAttack = JumpAttackVelocity;

        float speedMultiplier = 1 + accMultiplier;

        moveSpeed = speedMultiplier * moveSpeed;
        jumpForce = speedMultiplier * jumpForce;
        anim.speed = speedMultiplier * anim.speed;
        wallJumpForce = speedMultiplier * wallJumpForce;
        JumpAttackVelocity = speedMultiplier * JumpAttackVelocity;

        yield return new WaitForSeconds(duration);

        moveSpeed = originalMoveSpeed;
        jumpForce = originalJumpForce;
        anim.speed = originalAnimSpeed;
        wallJumpForce = originalWallJump;
        JumpAttackVelocity = originalJumpAttack;
        SpeedUpCo = null;
    }

    public override void EntityDeath()
    {
        base.EntityDeath();

        OnPlayerDeath?.Invoke();

        sr.enabled = false;

        Collider2D cd = GetComponent<Collider2D>();
        if (cd != null)
            cd.enabled = false;
    }

    public override void EntityRespawn()
    {
        base.EntityRespawn();
        sr.enabled = true;

        Collider2D cd = GetComponent<Collider2D>();
        if (cd != null)
            cd.enabled = true;

        stateMachine.ChangeState(idleState);
    }

    public void EnterAttackStateWithDelay()
    {
        if (queuedAttackCo != null)
        {
            StopCoroutine(queuedAttackCo);
        }

        queuedAttackCo = StartCoroutine(EnterAttackStateWithDelayCo());
    }

    public IEnumerator EnterAttackStateWithDelayCo()
    {
        yield return new WaitForEndOfFrame();
        stateMachine.ChangeState(basicAttackState);
    }

    private void TryInteract()
    {
        Transform closest = null;
        float closestDistance = Mathf.Infinity;
        Collider2D[] objectsAround = Physics2D.OverlapCircleAll(transform.position, 1f);

        foreach (var target in objectsAround)
        {
            IInteractable interactable = target.GetComponent<IInteractable>();
            if (interactable == null)
                continue;

            float distance = Vector2.Distance(transform.position, target.transform.position);

            if (distance < closestDistance)
            {
                closestDistance = distance;
                closest = target.transform;
            }
        }

        if (closest == null)
            return;

        closest.GetComponent<IInteractable>().Interact();
    }

    private void OnEnable()
    {
        if (useNetworkControl)
            return;

        input.Enable();

        input.Player.Movement.performed += ctx => moveInput = ctx.ReadValue<Vector2>();
        input.Player.Movement.canceled += ctx => moveInput = Vector2.zero;

        input.Player.Interact.performed += ctx => TryInteract();

        input.Player.QuickItemSlot_1.performed += ctx => inventory.TryUseQuickItemInSlot(1);
        input.Player.QuickItemSlot_2.performed += ctx => inventory.TryUseQuickItemInSlot(2);
    }

    public void TeleportPlayer(Vector3 position) => transform.position = position;

    protected override void Update()
    {
        base.Update();
        UpdateLocalInputCache();

        if (!isLocalPlayer)
            return;

        FlipCharacterTowardsMouse();
        AimTowardsMouse();
    }

    private void OnDisable()
    {
        input?.Disable();
    }

    private void UpdateLocalInputCache()
    {
        if (useNetworkControl)
        {
            cachedMoveInput = Vector2.zero;
            moveInput = Vector2.zero;
            jumpPressedThisFrame = false;
            dashPressedThisFrame = false;
            attackPressedThisFrame = false;
            attackHeld = false;
            return;
        }

        cachedMoveInput = input.Player.Movement.ReadValue<Vector2>();
        moveInput = cachedMoveInput;
        jumpPressedThisFrame = input.Player.Jump.WasPressedThisFrame();
        dashPressedThisFrame = input.Player.Dash.WasPressedThisFrame();
        attackPressedThisFrame = input.Player.Attack.WasPressedThisFrame();
        attackHeld = input.Player.Attack.IsPressed();
    }

    public string GetCurrentStateName()
    {
        return stateMachine?.currentState?.GetType().Name ?? "Unknown";
    }


    // 临时可编译版本：先按“当前状态是否属于地面态”来判断
    public bool GetIsGroundedForNet()
    {
        var current = stateMachine?.currentState;
        return current == idleState || current == moveState || current == basicAttackState || current == castingState;
    }


    public int GetJumpCountForNet()
    {
        return currentJumpCount;
    }

    public float GetCurrentVelY()
    {
        return rb != null ? rb.linearVelocity.y : 0f;
    }

    public void ApplyServerState(string acceptedState, bool acceptedGrounded, int acceptedJumpCount)
    {
        if (acceptedGrounded)
        {
            currentJumpCount = 0;
            return;
        }

        currentJumpCount = acceptedJumpCount;
    }

    public bool CanStartJump()
    {
        return currentJumpCount < maxJumpCount;
    }

    public void ConsumeJump()
    {
        currentJumpCount++;
    }

    public void ResetJumpCount()
    {
        currentJumpCount = 0;
    }

}