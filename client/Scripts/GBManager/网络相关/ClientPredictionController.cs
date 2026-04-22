using System;
using System.Collections.Generic;
using UnityEngine;

public class ClientPredictionController : MonoBehaviour
{
    [Header("引用")]
    [SerializeField] private Player player;

    [Header("参数")]
    [SerializeField] private float fixedDt = 0.02f;
    [SerializeField] private float moveSpeed = 8f;
    [SerializeField] private float gravity = -1.2f;
    [SerializeField] private float jumpVelocity = 10f;
    [SerializeField] private float fallSpeedCap = -18f;
    [SerializeField] private float snapThreshold = 1.0f;

    [Header("地图参数（需与服务器一致）")]
    [SerializeField] private float groundEpsilon = 0.2f;
    [SerializeField] private float playerHalfWidth = 0.4f;
    [SerializeField] private float playerHalfHeight = 0.9f;
    [SerializeField] private float offsetY = 0.7f;

    [Header("调试")]
    [SerializeField] private bool debugLog = true;

    private readonly List<PlayerInputCmd> pendingInputs = new List<PlayerInputCmd>();
    private PredictedPlayerState predictedState;

    private float GroundY => -1.45f + offsetY;
    private const int MaxJumpCount = 2;

    private Vector3 logicPos;

    [Serializable]
    private struct PlatformData
    {
        public float xMin;
        public float xMax;
        public float y;
        public string kind;

        public PlatformData(float xMin, float xMax, float y, string kind)
        {
            this.xMin = xMin;
            this.xMax = xMax;
            this.y = y;
            this.kind = kind;
        }
    }

    [Serializable]
    private struct RectColliderData
    {
        public float xMin;
        public float xMax;
        public float yMin;
        public float yMax;
        public string kind;

        public RectColliderData(float xMin, float xMax, float yMin, float yMax, string kind)
        {
            this.xMin = xMin;
            this.xMax = xMax;
            this.yMin = yMin;
            this.yMax = yMax;
            this.kind = kind;
        }
    }

    private List<PlatformData> mapPlatforms;
    private List<RectColliderData> mapWalls;

    private void Awake()
    {
        if (player == null)
            player = GetComponent<Player>();

        BuildMapData();

        Vector3 p = player.transform.position;

        predictedState.posX = p.x;
        predictedState.posY = TransformYToFootY(p.y);
        predictedState.velX = 0f;
        predictedState.velY = 0f;
        predictedState.grounded = true;
        predictedState.jumpCount = 0;
        predictedState.acceptedDrop = false;
        predictedState.stateName = "Grounded";
    }

    public void AddLocalInput(PlayerInputCmd cmd)
    {
        pendingInputs.Add(cmd);
        Simulate(ref predictedState, cmd);
        ApplyPredictedState();
    }

    public void Reconcile(MatchSnapshot snapshot)
    {
        if (snapshot == null)
            return;

        pendingInputs.RemoveAll(cmd => cmd.seq <= snapshot.lastProcessedSeq);

        float beforeX = predictedState.posX;
        float beforeY = predictedState.posY;

        predictedState.posX = snapshot.serverPosX;
        predictedState.posY = snapshot.serverPosY;
        predictedState.velX = snapshot.serverVelX;
        predictedState.velY = snapshot.serverVelY;
        predictedState.grounded = snapshot.acceptedGrounded;
        predictedState.jumpCount = snapshot.acceptedJumpCount;
        predictedState.acceptedDrop = snapshot.acceptedDrop;
        predictedState.stateName = snapshot.acceptedState;

        foreach (var cmd in pendingInputs)
        {
            Simulate(ref predictedState, cmd);
        }

        float error = Vector2.Distance(
            new Vector2(beforeX, beforeY),
            new Vector2(predictedState.posX, predictedState.posY)
        );

        if (debugLog)
        {
            Debug.Log(
                $"[Reconcile] ack={snapshot.lastProcessedSeq} " +
                $"pending={pendingInputs.Count} " +
                $"server=({snapshot.serverPosX:F3},{snapshot.serverPosY:F3}) " +
                $"replayed=({predictedState.posX:F3},{predictedState.posY:F3}) " +
                $"error={error:F3} grounded={predictedState.grounded} jumpCount={predictedState.jumpCount} velY={predictedState.velY:F3}"
            );
        }

        if (error > snapThreshold && debugLog)
        {
            Debug.Log("[Reconcile] Large error, snap to replayed result.");
        }

        ApplyPredictedState();
    }

    public PredictedPlayerState GetPredictedState()
    {
        return predictedState;
    }

    public bool CanJumpDebug()
    {
        return predictedState.grounded || predictedState.jumpCount < MaxJumpCount;
    }

    private void ApplyPredictedState()
    {
        if (player == null)
            return;

        float displayY = FootYToTransformY(predictedState.posY);
        logicPos = new Vector3(predictedState.posX, displayY, player.transform.position.z);

        // 关键：根节点直接放逻辑位置，不在这里做视觉平滑
        player.SetLogicalPosition(logicPos.x, logicPos.y);

        player.ApplyServerState(
            predictedState.stateName,
            predictedState.grounded,
            predictedState.jumpCount
        );
    }

    private void Simulate(ref PredictedPlayerState state, PlayerInputCmd cmd)
    {
        float inputX = Mathf.Clamp(cmd.moveX, -1f, 1f);
        bool jumpPressed = cmd.jumpPressed;
        bool downHeld = cmd.downHeld;
        bool dropPressed = cmd.dropPressed;

        state.acceptedDrop = false;

        // 1) 水平
        state.velX = inputX * moveSpeed;
        float nextX = state.posX + state.velX * fixedDt;

        if (!HitsWall(nextX, state.posY))
        {
            state.posX = nextX;
        }
        else
        {
            state.velX = 0f;
        }

        // 2) 先按当前状态刷新 grounded
        RefreshGroundedFromMap(ref state);

        // 3) drop-through
        PlatformData? currentPlatform = GetStandingPlatform(state.posX, state.posY);
        if (dropPressed && downHeld)
        {
            if (currentPlatform.HasValue && currentPlatform.Value.kind == "oneway")
            {
                state.acceptedDrop = true;
                state.grounded = false;
                state.stateName = "Fall";
                state.velY = Mathf.Min(state.velY, -2f);
                state.posY -= 0.15f;
            }
        }
        // 4) jump 前置
        else if (jumpPressed)
        {
            if (state.grounded)
            {
                state.grounded = false;
                state.jumpCount = 1;
                state.stateName = "Jump";
                state.velY = jumpVelocity;
            }
            else if (state.jumpCount < MaxJumpCount)
            {
                state.jumpCount += 1;
                state.stateName = "Jump";
                state.velY = jumpVelocity;
            }
        }

        // 5) 垂直推进
        StepVertical(ref state);

        // 6) 再刷新一次 grounded
        RefreshGroundedFromMap(ref state);

        // 7) grounded 下的水平状态兜底
        if (state.grounded)
        {
            if (Mathf.Abs(inputX) > 0.01f)
                state.stateName = "Player_MoveState";
            else
                state.stateName = "Player_IdleState";
        }

        if (debugLog && jumpPressed)
        {
            Debug.Log(
                $"[Predict Jump] jumpPressed={jumpPressed} grounded={state.grounded} jumpCount={state.jumpCount} " +
                $"pos=({state.posX:F3},{state.posY:F3}) vel=({state.velX:F3},{state.velY:F3}) state={state.stateName}"
            );
        }
    }

    private void RefreshGroundedFromMap(ref PredictedPlayerState state)
    {
        if (state.velY > 0f)
        {
            state.grounded = false;
            return;
        }

        PlatformData? standingPlatform = GetStandingPlatform(state.posX, state.posY);

        if (standingPlatform.HasValue)
        {
            state.grounded = true;
            state.velY = 0f;
            state.posY = standingPlatform.Value.y;

            if (state.stateName != "Dash" && state.stateName != "BasicAttack")
                state.stateName = "Grounded";

            state.jumpCount = 0;
        }
        else
        {
            state.grounded = false;

            if (state.stateName == "Grounded")
                state.stateName = "Airborne";
        }
    }

    private void StepVertical(ref PredictedPlayerState state)
    {
        PlatformData? standing = GetStandingPlatform(state.posX, state.posY);

        if (standing.HasValue && state.grounded && state.velY <= 0f)
        {
            state.posY = standing.Value.y;
            state.velY = 0f;
            return;
        }

        state.velY += gravity;
        if (state.velY < fallSpeedCap)
            state.velY = fallSpeedCap;

        float previousY = state.posY;
        float nextY = state.posY + state.velY * fixedDt;

        PlatformData? landing = FindLandingPlatform(state.posX, previousY, nextY);

        if (landing.HasValue && state.velY <= 0f)
        {
            state.posY = landing.Value.y;
            state.velY = 0f;
            state.grounded = true;

            if (state.stateName != "Dash" && state.stateName != "BasicAttack")
                state.stateName = "Grounded";
        }
        else
        {
            state.posY = nextY;
            state.grounded = false;

            if (state.velY < 0f &&
                state.stateName != "Jump" &&
                state.stateName != "Dash" &&
                state.stateName != "BasicAttack")
            {
                state.stateName = "Fall";
            }
        }
    }

    private bool HitsWall(float x, float footY)
    {
        float playerLeft = x - playerHalfWidth;
        float playerRight = x + playerHalfWidth;
        float playerBottom = footY;
        float playerTop = footY + playerHalfHeight * 2f;

        foreach (var wall in mapWalls)
        {
            bool overlapX = playerRight > wall.xMin && playerLeft < wall.xMax;
            bool overlapY = playerTop > wall.yMin && playerBottom < wall.yMax;

            if (overlapX && overlapY)
                return true;
        }

        return false;
    }

    private PlatformData? GetStandingPlatform(float x, float footY)
    {
        foreach (var platform in mapPlatforms)
        {
            if (IsOnPlatform(x, footY, platform))
                return platform;
        }

        return null;
    }

    private bool IsOnPlatform(float x, float footY, PlatformData platform)
    {
        bool withinX = (x + playerHalfWidth) >= platform.xMin &&
                       (x - playerHalfWidth) <= platform.xMax;

        bool closeY = Mathf.Abs(footY - platform.y) <= groundEpsilon;

        return withinX && closeY;
    }

    private PlatformData? FindLandingPlatform(float x, float previousY, float nextY)
    {
        List<PlatformData> candidates = new List<PlatformData>();

        foreach (var platform in mapPlatforms)
        {
            bool withinX = (x + playerHalfWidth) >= platform.xMin &&
                           (x - playerHalfWidth) <= platform.xMax;

            bool crossedY = previousY >= platform.y && platform.y >= nextY;

            if (withinX && crossedY)
                candidates.Add(platform);
        }

        if (candidates.Count == 0)
            return null;

        candidates.Sort((a, b) => b.y.CompareTo(a.y));
        return candidates[0];
    }

    private float TransformYToFootY(float transformY)
    {
        return transformY - playerHalfHeight;
    }

    private float FootYToTransformY(float footY)
    {
        return footY + playerHalfHeight;
    }

    private void BuildMapData()
    {
        mapPlatforms = new List<PlatformData>
        {
            new PlatformData(-9f,    29f,    GroundY,           "solid"),
            new PlatformData(-1.25f, 1.25f,  1.0f + offsetY,    "oneway"),
            new PlatformData(8.75f,  11.25f, 1.0f + offsetY,    "oneway"),
            new PlatformData(18.75f, 21.25f, 1.0f + offsetY,    "oneway"),
            new PlatformData(3.75f,  6.25f,  2.5f + offsetY,    "oneway"),
            new PlatformData(13.75f, 16.25f, 2.5f + offsetY,    "oneway"),
        };

        mapWalls = new List<RectColliderData>
        {
            new RectColliderData(-9.0f, -8.5f, GroundY, GroundY + 1.5f, "solid"),
            new RectColliderData(29.0f, 29.5f, GroundY, GroundY + 1.5f, "solid"),
        };
    }
}