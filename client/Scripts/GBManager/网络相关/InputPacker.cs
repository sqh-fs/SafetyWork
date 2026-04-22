using UnityEngine;
using UnityEngine.InputSystem;

public class InputPacker : MonoBehaviour
{
    [Header("调试")]
    [SerializeField] private bool debugInputLog = true;
    [SerializeField] private int logEveryNPackets = 10;

    private PlayerInputSet input;
    private int currentSeq = 0;

    [SerializeField] private RelayChatClient relayClient;
    [SerializeField] private Transform aimOrigin;
    [SerializeField] private Player player;
    [SerializeField] private ClientPredictionController predictionController;

    private float lastMeasuredX;
    private bool hasLastMeasuredX;

    // 关键：瞬时输入先缓存
    private bool jumpPressedBuffered;
    private bool dashPressedBuffered;
    private bool attackPressedBuffered;

    private void Awake()
    {
        input = new PlayerInputSet();

        if (aimOrigin == null)
            aimOrigin = transform;

        if (player == null)
            player = GetComponent<Player>();

        if (predictionController == null)
            predictionController = GetComponent<ClientPredictionController>();

        if (player != null)
        {
            lastMeasuredX = player.transform.position.x;
            hasLastMeasuredX = true;
        }
    }

    private void OnEnable()
    {
        input.Enable();

        input.Player.Jump.performed += OnJumpPerformed;
        input.Player.Dash.performed += OnDashPerformed;
        input.Player.Attack.performed += OnAttackPerformed;
    }

    private void OnDisable()
    {
        input.Player.Jump.performed -= OnJumpPerformed;
        input.Player.Dash.performed -= OnDashPerformed;
        input.Player.Attack.performed -= OnAttackPerformed;

        input.Disable();
    }

    private void OnJumpPerformed(InputAction.CallbackContext ctx)
    {
        jumpPressedBuffered = true;
        if (debugInputLog)
            Debug.Log("[InputPacker] Jump PERFORMED");
    }

    private void OnDashPerformed(InputAction.CallbackContext ctx)
    {
        dashPressedBuffered = true;
    }

    private void OnAttackPerformed(InputAction.CallbackContext ctx)
    {
        attackPressedBuffered = true;
    }

    private void FixedUpdate()
    {
        Vector2 move = input.Player.Movement.ReadValue<Vector2>();
        Vector2 aim = ReadAimDirection();

        // 从 buffer 取，而不是直接 WasPressedThisFrame()
        bool jumpPressed = jumpPressedBuffered;
        bool dashPressed = dashPressedBuffered;
        bool attackPressed = attackPressedBuffered;

        // 消费后清掉
        jumpPressedBuffered = false;
        dashPressedBuffered = false;
        attackPressedBuffered = false;

        bool attackHeld = input.Player.Attack.IsPressed();
        bool downHeld = move.y < -0.5f;
        bool dropPressed = downHeld && jumpPressed;

        float packedMoveX = Mathf.Clamp(move.x, -1f, 1f);

        float clientPosX = player != null ? player.transform.position.x : transform.position.x;
        float clientVelX = 0f;

        if (hasLastMeasuredX)
        {
            float dx = clientPosX - lastMeasuredX;
            clientVelX = dx / Time.fixedDeltaTime;
        }

        lastMeasuredX = clientPosX;
        hasLastMeasuredX = true;

        player?.ApplyNetworkInputFrame(
            move,
            jumpPressed,
            dashPressed,
            attackPressed,
            attackHeld,
            downHeld,
            dropPressed
        );

        var cmd = new PlayerInputCmd
        {
            seq = currentSeq++,
            moveX = packedMoveX,

            jumpPressed = jumpPressed,
            attackPressed = attackPressed,
            downHeld = downHeld,
            dropPressed = dropPressed,
            aimX = aim.x,
            aimY = aim.y,

            clientState = player != null ? player.GetCurrentStateName() : "Unknown",
            clientGrounded = player != null && player.GetIsGroundedForNet(),
            clientJumpCount = player != null ? player.GetJumpCountForNet() : 0,

            clientPosX = clientPosX,
            clientVelX = clientVelX
        };

        predictionController?.AddLocalInput(cmd);

        if (relayClient != null)
        {
            if (debugInputLog && (currentSeq == 1 || currentSeq % Mathf.Max(1, logEveryNPackets) == 0))
            {
                Debug.Log(
                    $"[InputPacker:{name}] send seq={cmd.seq} inputX={cmd.moveX:F2} " +
                    $"clientPosX={cmd.clientPosX:F3} clientVelX={cmd.clientVelX:F3} " +
                    $"jump={cmd.jumpPressed} attack={cmd.attackPressed} dash={dashPressed} " +
                    $"aim=({cmd.aimX:F2},{cmd.aimY:F2}) " +
                    $"state={cmd.clientState} grounded={cmd.clientGrounded} jumpCount={cmd.clientJumpCount}"
                );
            }

            _ = relayClient.SendInput(cmd);
        }
        else if (debugInputLog)
        {
            Debug.LogWarning($"[InputPacker:{name}] relayClient 没有绑定，输入包没有发出去。");
        }
    }

    private Vector2 ReadAimDirection()
    {
        if (Mouse.current == null || Camera.main == null || aimOrigin == null)
            return Vector2.right;

        Vector3 mouseScreen = Mouse.current.position.ReadValue();
        Vector3 mouseWorld = Camera.main.ScreenToWorldPoint(mouseScreen);
        mouseWorld.z = 0f;

        Vector2 direction = mouseWorld - aimOrigin.position;
        if (direction.sqrMagnitude < 0.0001f)
            return Vector2.right;

        return direction.normalized;
    }
}