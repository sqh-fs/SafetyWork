using System;
using System.Collections;
using PixelCrushers.DialogueSystem;
using Unity.VisualScripting;
using Unity.VisualScripting.Antlr3.Runtime;
using UnityEngine;
using UnityEngine.InputSystem;

public class Player : Entity
{
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
    public bool downHeld { get; private set; }
    public bool dropPressedThisFrame { get; private set; }

    public UI ui { get; private set; }

    [Header("Movement Details")]
    public float moveSpeed = 1.0f;
    public float jumpForce = 15f;
    public Vector2 wallJumpForce;
    public float dashDuration = .3f;
    public float dashSpeed = 25f;

    [Header("Jump Rules")]
    public int maxJumpCount = 2;
    [SerializeField] private int currentJumpCount = 0;

    public Vector2 moveInput { get; private set; }
    public Vector2 mousePosition { get; private set; }

    [Range(0, 1)] public float inAirMoveMultiplier = .7f;
    [Range(0, 1)] public float wallSlideSlowMultiplier = .7f;

    [Header("One Way Platform")]
    [SerializeField] private LayerMask oneWayPlatformMask;
    [SerializeField] private Collider2D bodyCollider;
    [SerializeField] private float dropThroughDuration = 0.2f;

    private bool isDroppingThrough;

    [Header("Attack Details")]
    public Vector2[] attackVelocity;
    public Vector2 JumpAttackVelocity;
    public float attackVelocityDuration = .1f;
    public float comboResetTime = 1;
    private Coroutine queuedAttackCo;

    [Header("是否网络测试Control")]
    public bool useNetworkControl = true;

    [Header("服务器同步")]
    [SerializeField] private bool debugServerState = true;
    [SerializeField] private bool forceAcceptServerState = false;

    [Header("网络预测")]
    [SerializeField] private ClientPredictionController predictionController;

    // 网络输入桥接
    private Vector2 netMoveInput;
    private bool netJumpPressedThisFrame;
    private bool netDashPressedThisFrame;
    private bool netAttackPressedThisFrame;
    private bool netAttackHeld;
    private bool netDownHeld;
    private bool netDropPressedThisFrame;

    private PlayerCombatController weaponController;

    [Header("Visual Smoothing")]
    [SerializeField] private Transform visualRoot;
    [SerializeField] private float visualFollowSharpness = 14f;
    [SerializeField] private float visualSnapDistance = 0.18f;

    private bool visualInitialized;

    protected override void Awake()
    {
        base.Awake();
        instance = this;
        input = new PlayerInputSet();
        stats = GetComponent<Player_Stats>();
        ui = FindAnyObjectByType<UI>();
        statusHandler = GetComponent<Entity_StatusHandler>();
        health = GetComponent<Entity_Health>();
        combat = GetComponent<Player_Combat>();
        inventory = GetComponent<Inventory_Player>();
        selector = GetComponent<ProximitySelector>();
        questManager = GetComponent<Player_QuestManager>();
        playerExp = GetComponent<Player_Exp>();
        weaponController = GetComponent<PlayerCombatController>();

        if (predictionController == null)
            predictionController = GetComponent<ClientPredictionController>();

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

    protected override void Start()
    {
        base.Start();
        stateMachine.Initialize(idleState);

        if (currentWeaponData != null)
            EquipWeapon(currentWeaponData);
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

    private void OnDisable()
    {
        input?.Disable();
    }

    protected override void Update()
    {
        base.Update();
        UpdateLocalInputCache();

        if (!isLocalPlayer)
            return;

        FlipCharacterTowardsMouse();
        AimTowardsMouse();
    }

    private void LateUpdate()
    {
        if (!useNetworkControl || predictionController == null || anim == null)
            return;

        var ps = predictionController.GetPredictedState();

        anim.SetFloat("xVelocity", Mathf.Abs(ps.velX));
        anim.SetFloat("yVelocity", ps.velY);
        anim.SetBool("grounded", ps.grounded);

        SmoothVisualToLogical();
    }

    public void ApplyNetworkInputFrame(
        Vector2 move,
        bool jumpPressed,
        bool dashPressed,
        bool attackPressed,
        bool attackHeldValue,
        bool downHeldValue,
        bool dropPressed)
    {
        netMoveInput = move;
        netJumpPressedThisFrame = jumpPressed;
        netDashPressedThisFrame = dashPressed;
        netAttackPressedThisFrame = attackPressed;
        netAttackHeld = attackHeldValue;
        netDownHeld = downHeldValue;
        netDropPressedThisFrame = dropPressed;
    }

    private void UpdateLocalInputCache()
    {
        if (useNetworkControl)
        {
            cachedMoveInput = netMoveInput;
            moveInput = netMoveInput;
            jumpPressedThisFrame = netJumpPressedThisFrame;
            dashPressedThisFrame = netDashPressedThisFrame;
            attackPressedThisFrame = netAttackPressedThisFrame;
            attackHeld = netAttackHeld;
            downHeld = netDownHeld;
            dropPressedThisFrame = netDropPressedThisFrame;

            netJumpPressedThisFrame = false;
            netDashPressedThisFrame = false;
            netAttackPressedThisFrame = false;
            netDropPressedThisFrame = false;
            return;
        }

        cachedMoveInput = input.Player.Movement.ReadValue<Vector2>();
        moveInput = cachedMoveInput;
        jumpPressedThisFrame = input.Player.Jump.WasPressedThisFrame();
        dashPressedThisFrame = input.Player.Dash.WasPressedThisFrame();
        attackPressedThisFrame = input.Player.Attack.WasPressedThisFrame();
        attackHeld = input.Player.Attack.IsPressed();

        downHeld = cachedMoveInput.y < -0.5f;
        dropPressedThisFrame = downHeld && jumpPressedThisFrame;
    }

    public void ApplyPredictedMotion(
        float posX,
        float displayY,
        float velX,
        float velY,
        bool grounded,
        int jumpCount,
        string stateName)
    {
        transform.position = new Vector3(posX, displayY, transform.position.z);
        currentJumpCount = jumpCount;

        if (Mathf.Abs(velX) > 0.01f)
            HandleFlip(velX);

        ApplyServerState(stateName, grounded, jumpCount);
    }

    public string GetCurrentStateName()
    {
        return stateMachine?.currentState?.GetType().Name ?? "Unknown";
    }

    public bool GetIsGroundedForNet()
    {
        if (useNetworkControl && predictionController != null)
            return predictionController.GetPredictedState().grounded;

        var current = stateMachine?.currentState;
        return current == idleState || current == moveState || current == basicAttackState || current == castingState;
    }

    public int GetJumpCountForNet()
    {
        if (useNetworkControl && predictionController != null)
            return predictionController.GetPredictedState().jumpCount;

        return currentJumpCount;
    }

    public float GetCurrentVelY()
    {
        if (useNetworkControl && predictionController != null)
            return predictionController.GetPredictedState().velY;

        return rb != null ? rb.linearVelocity.y : 0f;
    }

    public bool CanStartJump()
    {
        if (useNetworkControl && predictionController != null)
            return predictionController.CanJumpDebug();

        return currentJumpCount < maxJumpCount;
    }

    public void ConsumeJump()
    {
        if (useNetworkControl)
            return;

        currentJumpCount++;
    }

    public void ResetJumpCount()
    {
        if (useNetworkControl)
            return;

        currentJumpCount = 0;
    }

    public void TryDropThroughOneWay()
    {
        if (useNetworkControl)
            return;

        if (!CanDropThroughOneWay())
            return;

        RaycastHit2D hit = GetOneWayGroundHit();
        if (hit.collider == null)
            return;

        StartCoroutine(DropThroughOneWayCo(hit.collider));
    }

    private IEnumerator DropThroughOneWayCo(Collider2D platformCollider)
    {
        isDroppingThrough = true;

        Physics2D.IgnoreCollision(bodyCollider, platformCollider, true);

        Vector2 v = rb.linearVelocity;
        if (v.y > 0f) v.y = 0f;
        v.y = -2f;
        rb.linearVelocity = v;

        yield return new WaitForSeconds(dropThroughDuration);

        if (bodyCollider != null && platformCollider != null)
            Physics2D.IgnoreCollision(bodyCollider, platformCollider, false);

        isDroppingThrough = false;
    }

    public bool CanDropThroughOneWay()
    {
        if (useNetworkControl)
            return false;

        if (isDroppingThrough)
            return false;

        if (!groundDetected)
            return false;

        if (!dropPressedThisFrame)
            return false;

        if (!IsStandingOnOneWayPlatform())
            return false;

        return true;
    }

    public RaycastHit2D GetOneWayGroundHit()
    {
        return Physics2D.Raycast(
            groundCheck.position,
            Vector2.down,
            groundCheckDistance,
            oneWayPlatformMask
        );
    }

    public bool IsStandingOnOneWayPlatform()
    {
        return GetOneWayGroundHit().collider != null;
    }

    public void ApplyServerPosition(float serverPosX, float serverPosY, float serverVelY)
    {
        Debug.Log($"[Player] ApplyServerPosition target=({serverPosX}, {serverPosY}) current={transform.position}");
        transform.position = new Vector3(serverPosX, serverPosY, transform.position.z);
    }

    public void ApplyServerState(string acceptedState, bool acceptedGrounded, int acceptedJumpCount)
    {
        currentJumpCount = acceptedJumpCount;

        if (debugServerState)
        {
            Debug.Log(
                $"[Player] ApplyServerState state={acceptedState} grounded={acceptedGrounded} jumpCount={acceptedJumpCount} force={forceAcceptServerState}"
            );
        }

        if (stateMachine == null || idleState == null || moveState == null || jumpState == null || fallState == null)
            return;

        // 关键：默认不强制切状态
        if (!forceAcceptServerState)
            return;

        if (acceptedGrounded)
        {
            if (Mathf.Abs(moveInput.x) > 0.01f)
            {
                if (!(stateMachine.currentState is Player_MoveState))
                    stateMachine.ChangeState(moveState);
            }
            else
            {
                if (!(stateMachine.currentState is Player_IdleState))
                    stateMachine.ChangeState(idleState);
            }
            return;
        }

        if (acceptedState == "Jump")
        {
            if (!(stateMachine.currentState is Player_JumpState))
                stateMachine.ChangeState(jumpState);
        }
        else
        {
            if (!(stateMachine.currentState is Player_FallState))
                stateMachine.ChangeState(fallState);
        }
    }

    public override void HandleFlip(float xVelocity)
    {
        // 保持留空，角色朝向由鼠标控制
    }

    private void FlipCharacterTowardsMouse()
    {
        if (Mouse.current == null)
            return;

        Vector2 mouseScreenPosition = Mouse.current.position.ReadValue();
        Vector3 mousePosition = Camera.main.ScreenToWorldPoint(mouseScreenPosition);

        if (mousePosition.x > transform.position.x && !facingRight)
            Flip();
        else if (mousePosition.x < transform.position.x && facingRight)
            Flip();
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
            idleHoldPoint.localRotation = Quaternion.identity;
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

    public void EnterAttackStateWithDelay()
    {
        if (queuedAttackCo != null)
            StopCoroutine(queuedAttackCo);

        queuedAttackCo = StartCoroutine(EnterAttackStateWithDelayCo());
    }

    public IEnumerator EnterAttackStateWithDelayCo()
    {
        yield return new WaitForEndOfFrame();
        stateMachine.ChangeState(basicAttackState);
    }

    public void TeleportPlayer(Vector3 position) => transform.position = position;

    public void EquipWeapon(WeaponDataSO newWeaponData)
    {
        if (currentWeaponData != null)
            ((Entity_Stats)stats).RemoveWeaponModifiers(currentWeaponData);

        currentWeaponData = newWeaponData;

        if (currentWeaponInstance != null)
            Destroy(currentWeaponInstance.gameObject);

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
            ((Entity_Stats)stats).ApplyWeaponModifiers(currentWeaponData);
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

    public void SetLogicalPosition(float x, float y)
    {
        Vector3 oldWorldPos = transform.position;
        Vector3 newWorldPos = new Vector3(x, y, transform.position.z);
        Vector3 worldDelta = newWorldPos - oldWorldPos;

        // 先移动逻辑根节点
        transform.position = newWorldPos;

        // 关键：如果视觉节点存在，就把它反向偏移，抵消这次根节点瞬移
        if (visualRoot != null)
        {
            // 转成父节点局部空间里的位移
            Vector3 localDelta = transform.InverseTransformVector(worldDelta);
            visualRoot.localPosition -= localDelta;

            // 可选：限制最大视觉偏移，避免极端情况下拖得太夸张
            float maxVisualOffset = 0.35f;
            if (visualRoot.localPosition.magnitude > maxVisualOffset)
            {
                visualRoot.localPosition = visualRoot.localPosition.normalized * maxVisualOffset;
            }
        }
    }

    public void SmoothVisualToLogical()
    {
        if (visualRoot == null)
            return;

        if (!visualInitialized)
        {
            visualRoot.localPosition = Vector3.zero;
            visualInitialized = true;
            return;
        }

        Vector3 targetLocal = Vector3.zero;

        float dist = Vector3.Distance(visualRoot.localPosition, targetLocal);

        if (dist > visualSnapDistance)
        {
            visualRoot.localPosition = targetLocal;
            return;
        }

        float t = 1f - Mathf.Exp(-visualFollowSharpness * Time.deltaTime);
        visualRoot.localPosition = Vector3.Lerp(visualRoot.localPosition, targetLocal, t);
    }


}