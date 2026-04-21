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

    private void Awake()
    {
        input = new PlayerInputSet();

        if (aimOrigin == null)
            aimOrigin = transform;

        if (player == null)
            player = GetComponent<Player>();
    }

    private void OnEnable()
    {
        input.Enable();
    }

    private void OnDisable()
    {
        input.Disable();
    }

    private void FixedUpdate()
    {
        Vector2 move = input.Player.Movement.ReadValue<Vector2>();
        Vector2 aim = ReadAimDirection();

        var cmd = new PlayerInputCmd
        {
            seq = currentSeq++,
            moveX = move.x,
    
            jumpPressed = input.Player.Jump.WasPressedThisFrame(),
            attackPressed = input.Player.Attack.WasPressedThisFrame(),
            aimX = aim.x,
            aimY = aim.y,

            clientState = player != null ? player.GetCurrentStateName() : "Unknown",
            clientGrounded = player != null && player.GetIsGroundedForNet(),
            clientJumpCount = player != null ? player.GetJumpCountForNet() : 0
        };

        if (relayClient != null)
        {
            if (debugInputLog && (currentSeq == 1 || currentSeq % Mathf.Max(1, logEveryNPackets) == 0))
            {
                Debug.Log(
                    $"[InputPacker:{name}] send seq={cmd.seq} moveX={cmd.moveX:F2} " +
                    $"jump={cmd.jumpPressed} attack={cmd.attackPressed} aim=({cmd.aimX:F2},{cmd.aimY:F2}) " +
                    $"state={cmd.clientState} grounded={cmd.clientGrounded} jumpCount={cmd.clientJumpCount}");
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