using System;
using System.Text;
using System.Threading.Tasks;
using NativeWebSocket;
using UnityEngine;
using UnityEngine.SceneManagement;

public class RelayChatClient : MonoBehaviour
{
    [Header("连接配置")]
    [SerializeField] private string serverUrl = "ws://127.0.0.1:8765";

    [Tooltip("只作为初始默认值。最终身份必须以服务器 ROOM_STATE / GAME_START 返回为准。")]
    [SerializeField] private string clientId = "";

    [SerializeField] private string roomId = "";

    [Tooltip("MainMenu 可以 true，只连接服务器；MainGame 建议 false，由 MainGameNetworkBootstrap 控制。")]
    [SerializeField] private bool autoConnect = false;

    [Tooltip("MainMenu 必须 false。MainGame 也建议 false，由 MainGameNetworkBootstrap 控制。")]
    [SerializeField] private bool autoJoin = false;

    [Header("调试")]
    [SerializeField] private bool debugNetworkLog = true;
    [SerializeField] private int logEveryNInputs = 10;

    [Header("Network Simulation")]
    [SerializeField] private bool simulateNetwork = false;
    [SerializeField] private int outgoingDelayMs = 120;
    [SerializeField] private int incomingDelayMs = 120;
    [SerializeField] private int jitterMs = 20;
    [SerializeField, Range(0f, 1f)] private float outgoingDropRate = 0f;
    [SerializeField, Range(0f, 1f)] private float incomingDropRate = 0f;
    public static RelayChatClient Instance { get; private set; }
    public string ClientId => clientId;
    public string RoomId => roomId;
    public bool IsConnected => websocket != null && websocket.State == WebSocketState.Open;
    public bool HasJoinedRoom => hasJoinedRoom;

    public event Action<RoomStatePayload> OnRoomStateReceived;
    public event Action<GameStartPayload> OnGameStartReceived;

    private WebSocket websocket;
    private bool hasJoinedRoom;

    private int sentInputCount;
    private int latestAppliedAck = -1;
    private int latestAppliedTick = -1;
    private bool applicationQuitting = false;
    [Serializable]
    private class NetMessage
    {
        public string type;
        public string roomId;
        public string clientId;
        public string targetId;
        public string text;
        public string fromClientId;
        public string timestamp;
        public string error;
        public string payload;
    }

    [Serializable]
    private class ChatPayload
    {
        public string text;
    }

    private void Awake()
    {
        if (Instance != null && Instance != this)
        {
            Debug.LogWarning(
                $"[RelayChatClient] Duplicate instance destroyed. " +
                $"old={Instance.gameObject.name}, new={gameObject.name}"
            );

            Destroy(gameObject);
            return;
        }

        Instance = this;

        // 保险：如果它挂在 Canvas / Manager 的子物体下面，先脱离父物体。
        transform.SetParent(null);

        DontDestroyOnLoad(gameObject);

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[RelayChatClient] Awake persistent instance. " +
                $"object={gameObject.name}, instance={GetInstanceID()}, " +
                $"scene={SceneManager.GetActiveScene().name}"
            );
        }
    }
    private void Start()
    {
        ApplyNetworkSessionToLocalFields();

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[RelayChatClient] Start object={gameObject.name}, " +
                $"instanceId={GetInstanceID()}, " +
                $"scene={SceneManager.GetActiveScene().name}, " +
                $"clientId={clientId}, roomId={roomId}, " +
                $"autoConnect={autoConnect}, autoJoin={autoJoin}"
            );
        }

        if (autoConnect)
            _ = Connect();
    }
    private void ApplyNetworkSessionToLocalFields()
    {
        if (NetworkSession.Instance == null)
            return;

        if (!string.IsNullOrWhiteSpace(NetworkSession.Instance.serverUrl))
            serverUrl = NetworkSession.Instance.serverUrl;

        if (!string.IsNullOrWhiteSpace(NetworkSession.Instance.roomId))
            roomId = NetworkSession.Instance.roomId;

        if (!string.IsNullOrWhiteSpace(NetworkSession.Instance.clientId))
            clientId = NetworkSession.Instance.clientId;
    }

    public void ConfigureIdentity(string newServerUrl, string newClientId, string newRoomId)
    {
        if (!string.IsNullOrWhiteSpace(newServerUrl))
            serverUrl = newServerUrl.Trim();

        if (newClientId != null)
            clientId = newClientId.Trim();

        if (newRoomId != null)
            roomId = newRoomId.Trim().ToUpper();

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[RelayChatClient] ConfigureIdentity " +
                $"server={serverUrl}, client={clientId}, room={roomId}"
            );
        }
    }

    public async Task Connect()
    {
        if (websocket != null &&
            (websocket.State == WebSocketState.Open ||
             websocket.State == WebSocketState.Connecting))
        {
            if (debugNetworkLog)
            {
                Debug.LogWarning(
                    $"[{clientId}] WebSocket already open/connecting. " +
                    $"state={websocket.State}, instance={GetInstanceID()}"
                );
            }

            return;
        }

        ApplyNetworkSessionToLocalFields();

        websocket = new WebSocket(serverUrl);

        websocket.OnOpen += () =>
        {
            Debug.Log(
                $"[{clientId}] WebSocket OPEN server={serverUrl}, " +
                $"instance={GetInstanceID()}, scene={SceneManager.GetActiveScene().name}"
            );

            if (autoJoin)
            {
                ApplyNetworkSessionToLocalFields();

                if (string.IsNullOrWhiteSpace(roomId))
                {
                    Debug.LogWarning($"[{clientId}] autoJoin=true but roomId is empty, skip JOIN_ROOM.");
                    return;
                }

                _ = SendJoinRoomManual(clientId, roomId);
            }
        };

        websocket.OnError += (errorMsg) =>
        {
            Debug.LogError($"[{clientId}] WebSocket ERROR: {errorMsg}");
        };

        websocket.OnClose += (closeCode) =>
        {
            Debug.LogWarning(
                $"[{clientId}] WebSocket CLOSED code={closeCode}, " +
                $"instance={GetInstanceID()}, scene={SceneManager.GetActiveScene().name}"
            );

            hasJoinedRoom = false;
        };

        websocket.OnMessage += (bytes) =>
        {
            string text = Encoding.UTF8.GetString(bytes);
            HandleIncomingMessage(text);
        };

        Debug.Log(
            $"[{clientId}] WebSocket CONNECTING server={serverUrl}, " +
            $"instance={GetInstanceID()}, scene={SceneManager.GetActiveScene().name}"
        );

        await websocket.Connect();
    }
    private async Task EnsureConnected()
    {
        if (websocket != null && websocket.State == WebSocketState.Open)
            return;

        if (websocket != null && websocket.State == WebSocketState.Connecting)
        {
            await WaitUntilSocketOpen();
            return;
        }

        await Connect();
        await WaitUntilSocketOpen();
    }

    private async Task WaitUntilSocketOpen(int timeoutMs = 3000)
    {
        int elapsed = 0;

        while (websocket != null &&
               websocket.State == WebSocketState.Connecting &&
               elapsed < timeoutMs)
        {
            await Task.Delay(50);
            elapsed += 50;
        }

        if (websocket == null || websocket.State != WebSocketState.Open)
        {
            Debug.LogWarning($"[{clientId}] WebSocket 等待 Open 超时，当前状态={websocket?.State}");
        }
    }

    public async Task SendCreateRoom()
    {
        await EnsureConnected();

        if (!EnsureSocketOpen())
            return;

        // 创建新房间时，本地先清掉旧房间状态。
        // 最终 clientId/roomId 等服务器 ROOM_STATE 返回后覆盖。
        roomId = "";
        clientId = "";
        hasJoinedRoom = false;

        if (NetworkSession.Instance != null)
            NetworkSession.Instance.Clear();

        var msg = new NetMessage
        {
            type = "CREATE_ROOM",
            roomId = "",
            clientId = "",
            payload = "{}"
        };

        await SendJson(msg, bypassSimulation: false);

        if (debugNetworkLog)
            Debug.Log("[RelayChatClient] SEND CREATE_ROOM");
    }

    public async Task SendJoinRoomManual(string wantedClientId, string wantedRoomId)
    {
        await EnsureConnected();

        if (!EnsureSocketOpen())
            return;

        string nextClientId = wantedClientId != null ? wantedClientId.Trim() : clientId;
        string nextRoomId = wantedRoomId != null ? wantedRoomId.Trim().ToUpper() : roomId;

        if (string.IsNullOrWhiteSpace(nextRoomId))
        {
            Debug.LogWarning($"[{clientId}] JOIN_ROOM failed: roomId is empty.");
            return;
        }

        if (hasJoinedRoom &&
            string.Equals(roomId, nextRoomId, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(clientId, nextClientId, StringComparison.OrdinalIgnoreCase))
        {
            if (debugNetworkLog)
            {
                Debug.Log(
                    $"[{clientId}] Skip duplicate JOIN_ROOM. " +
                    $"room={roomId}, instance={GetInstanceID()}"
                );
            }

            return;
        }

        clientId = nextClientId;
        roomId = nextRoomId;

        var msg = new NetMessage
        {
            type = "JOIN_ROOM",
            roomId = roomId,
            clientId = clientId,
            payload = "{}"
        };

        await SendJson(msg, bypassSimulation: false);

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[{clientId}] SEND JOIN_ROOM room={roomId}, " +
                $"requestedClient={clientId}, instance={GetInstanceID()}"
            );
        }
    }
    public async Task SendReady(bool ready)
    {
        if (!EnsureSocketOpen())
            return;

        if (string.IsNullOrWhiteSpace(roomId))
        {
            Debug.LogWarning($"[{clientId}] READY 失败：roomId 为空。");
            return;
        }

        var payload = new ReadyPayload
        {
            ready = ready
        };

        var msg = new NetMessage
        {
            type = "READY",
            roomId = roomId,
            clientId = clientId,
            payload = JsonUtility.ToJson(payload)
        };

        await SendJson(msg, bypassSimulation: false);

        if (debugNetworkLog)
            Debug.Log($"[{clientId}] SEND READY ready={ready}");
    }

    public async Task SendStartGame()
    {
        if (!EnsureSocketOpen())
            return;

        if (string.IsNullOrWhiteSpace(roomId))
        {
            Debug.LogWarning($"[{clientId}] START_GAME 失败：roomId 为空。");
            return;
        }

        var msg = new NetMessage
        {
            type = "START_GAME",
            roomId = roomId,
            clientId = clientId,
            payload = "{}"
        };

        await SendJson(msg, bypassSimulation: false);

        if (debugNetworkLog)
            Debug.Log($"[{clientId}] SEND START_GAME room={roomId}");
    }

    public async Task LeaveRoom()
    {
        if (!EnsureSocketOpen())
        {
            hasJoinedRoom = false;
            return;
        }

        if (string.IsNullOrWhiteSpace(roomId))
        {
            hasJoinedRoom = false;
            return;
        }

        var leave = new NetMessage
        {
            type = "LEAVE_ROOM",
            roomId = roomId,
            clientId = clientId,
            payload = "{}"
        };

        await SendJson(leave, bypassSimulation: false);

        hasJoinedRoom = false;

        if (debugNetworkLog)
            Debug.Log($"[{clientId}] SEND LEAVE_ROOM room={roomId}");
    }

    public async Task SendChat(string messageText)
    {
        if (string.IsNullOrWhiteSpace(messageText))
        {
            Debug.LogWarning($"[{clientId}] CHAT 内容为空，已忽略。");
            return;
        }

        if (!EnsureSocketOpen())
            return;

        if (!hasJoinedRoom)
        {
            Debug.LogWarning($"[{clientId}] 尚未加入房间，无法发送 CHAT。");
            return;
        }

        var payload = new ChatPayload
        {
            text = messageText
        };

        var chat = new NetMessage
        {
            type = "CHAT",
            roomId = roomId,
            clientId = clientId,
            payload = JsonUtility.ToJson(payload)
        };

        await SendJson(chat, bypassSimulation: false);

        if (debugNetworkLog)
            Debug.Log($"[{clientId}] SEND CHAT -> Server: {messageText}");
    }

    public async Task SendInput(PlayerInputCmd cmd)
    {
        if (!EnsureSocketOpen())
            return;

        if (!hasJoinedRoom)
            return;

        var msg = new NetMessage
        {
            type = "INPUT",
            roomId = roomId,
            clientId = clientId,
            payload = JsonUtility.ToJson(cmd)
        };

        sentInputCount++;

        if (debugNetworkLog &&
            (sentInputCount == 1 || sentInputCount % Mathf.Max(1, logEveryNInputs) == 0))
        {
            Debug.Log(
                $"[{clientId}] SEND INPUT #{sentInputCount} " +
                $"seq={cmd.seq} tick={cmd.tick} payload={msg.payload}"
            );
        }

        await SendJson(msg, bypassSimulation: false);
    }

    public async Task Disconnect()
    {
        WebSocket socketToClose = websocket;

        websocket = null;
        hasJoinedRoom = false;

        if (socketToClose == null)
            return;

        try
        {
            if (socketToClose.State == WebSocketState.Open ||
                socketToClose.State == WebSocketState.Connecting)
            {
                await socketToClose.Close();
            }
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[{clientId}] Disconnect exception: {ex.Message}");
        }

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[{clientId}] Disconnect done. " +
                $"instance={GetInstanceID()}, scene={SceneManager.GetActiveScene().name}"
            );
        }
    }
    private async Task SendJson(NetMessage message, bool bypassSimulation)
    {
        if (websocket == null)
        {
            Debug.LogWarning($"[{clientId}] websocket 为空，发送失败。");
            return;
        }

        string json = JsonUtility.ToJson(message);

        if (bypassSimulation || !simulateNetwork)
        {
            await SafeSendText(json);
            return;
        }

        if (UnityEngine.Random.value < outgoingDropRate)
        {
            if (debugNetworkLog)
                Debug.LogWarning($"[{clientId}] [NETSIM] OUT DROP type={message.type}");

            return;
        }

        int delay = GetSimulatedDelay(outgoingDelayMs, jitterMs);

        if (delay > 0)
        {
            if (debugNetworkLog)
                Debug.Log($"[{clientId}] [NETSIM] OUT delay={delay}ms type={message.type}");

            await Task.Delay(delay);
        }

        await SafeSendText(json);
    }

    private async Task SafeSendText(string json)
    {
        if (websocket == null || websocket.State != WebSocketState.Open)
        {
            Debug.LogWarning($"[{clientId}] 发送时 WebSocket 不可用。");
            return;
        }

        await websocket.SendText(json);
    }

    private void HandleIncomingMessage(string json)
    {
        _ = HandleIncomingMessageAsync(json);
    }

    private async Task HandleIncomingMessageAsync(string json)
    {
        if (debugNetworkLog)
            Debug.Log($"[{clientId}] RECV RAW {json}");

        NetMessage msg;

        try
        {
            msg = JsonUtility.FromJson<NetMessage>(json);
        }
        catch (Exception ex)
        {
            Debug.LogError($"[{clientId}] 解析顶层消息失败: {ex.Message}\nRaw={json}");
            return;
        }

        if (msg == null || string.IsNullOrWhiteSpace(msg.type))
            return;

        switch (msg.type)
        {
            case "ROOM_STATE":
                {
                    RoomStatePayload state;

                    try
                    {
                        state = JsonUtility.FromJson<RoomStatePayload>(msg.payload);
                    }
                    catch (Exception ex)
                    {
                        Debug.LogError($"[{clientId}] 解析 ROOM_STATE 失败: {ex.Message}\nPayload={msg.payload}");
                        return;
                    }

                    if (state == null)
                        return;

                    ApplyRoomStateToClient(state, "ROOM_STATE");
                    OnRoomStateReceived?.Invoke(state);

                    if (debugNetworkLog)
                    {
                        Debug.Log(
                            $"[{clientId}] ROOM_STATE room={state.roomId} " +
                            $"localClientId={state.localClientId} " +
                            $"slot={state.localSlotNo} " +
                            $"isHost={state.localIsHost} " +
                            $"canStart={state.canStart}"
                        );
                    }

                    return;
                }

            case "GAME_START":
                {
                    GameStartPayload start;

                    try
                    {
                        start = JsonUtility.FromJson<GameStartPayload>(msg.payload);
                    }
                    catch (Exception ex)
                    {
                        Debug.LogError($"[{clientId}] 解析 GAME_START 失败: {ex.Message}\nPayload={msg.payload}");
                        return;
                    }

                    if (start == null)
                        return;

                    ApplyGameStartToClient(start);
                    OnGameStartReceived?.Invoke(start);

                    string sceneName = string.IsNullOrWhiteSpace(start.sceneName)
                        ? "MainGame"
                        : start.sceneName;

                    if (debugNetworkLog)
                    {
                        Debug.Log(
                            $"[{clientId}] GAME_START -> LoadScene {sceneName}, " +
                            $"sessionClient={NetworkSession.Instance?.clientId}, " +
                            $"slot={NetworkSession.Instance?.slotNo}"
                        );
                    }

                    SceneManager.LoadScene(sceneName);
                    return;
                }

            case "SNAPSHOT":
                {
                    MatchSnapshot snapshot;

                    try
                    {
                        snapshot = JsonUtility.FromJson<MatchSnapshot>(msg.payload);
                    }
                    catch (Exception ex)
                    {
                        Debug.LogError($"[{clientId}] 解析 SNAPSHOT payload 失败: {ex.Message}\nPayload={msg.payload}");
                        return;
                    }

                    if (snapshot == null)
                        return;

                    if (debugNetworkLog)
                    {
                        Debug.Log(
                            $"[{clientId}] SNAPSHOT RAW tick={snapshot.tick} ack={snapshot.lastProcessedSeq} " +
                            $"players={(snapshot.players != null ? snapshot.players.Length : 0)} " +
                            $"projectiles={(snapshot.projectiles != null ? snapshot.projectiles.Length : 0)} " +
                            $"events={(snapshot.events != null ? snapshot.events.Length : 0)} " +
                            $"reject={snapshot.rejectReason}"
                        );
                    }

                    await DispatchSnapshotWithSimulation(snapshot);
                    return;
                }

            case "SERVER_BROADCAST":
                {
                    if (debugNetworkLog)
                        Debug.Log($"[{clientId}] SERVER_BROADCAST from={msg.fromClientId} text={msg.text}");

                    return;
                }

            case "ERROR":
                {
                    Debug.LogError($"[{clientId}] SERVER ERROR: {msg.error}");
                    return;
                }

            default:
                {
                    if (debugNetworkLog)
                        Debug.Log($"[{clientId}] UNHANDLED MSG type={msg.type}");

                    return;
                }
        }
    }

    private void ApplyRoomStateToClient(RoomStatePayload state, string reason)
    {
        if (state == null)
            return;

        roomId = state.roomId;

        // 关键：服务器返回的 localClientId 永远覆盖本地旧 clientId。
        // 这里不能写成“只有空才赋值”。
        if (!string.IsNullOrWhiteSpace(state.localClientId))
            clientId = state.localClientId;

        hasJoinedRoom =
            !string.IsNullOrWhiteSpace(roomId) &&
            !string.IsNullOrWhiteSpace(clientId);

        if (NetworkSession.Instance != null)
            NetworkSession.Instance.ApplyRoomState(state);

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[RelayChatClient] ApplyRoomState reason={reason} " +
                $"room={roomId}, client={clientId}, joined={hasJoinedRoom}, " +
                $"sessionClient={NetworkSession.Instance?.clientId}, " +
                $"slot={NetworkSession.Instance?.slotNo}"
            );
        }
    }

    private void ApplyGameStartToClient(GameStartPayload start)
    {
        if (start == null)
            return;

        roomId = start.roomId;

        if (!string.IsNullOrWhiteSpace(start.localClientId))
            clientId = start.localClientId;

        hasJoinedRoom =
            !string.IsNullOrWhiteSpace(roomId) &&
            !string.IsNullOrWhiteSpace(clientId);

        if (NetworkSession.Instance != null)
            NetworkSession.Instance.ApplyGameStart(start);

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[RelayChatClient] ApplyGameStart " +
                $"room={roomId}, client={clientId}, joined={hasJoinedRoom}, " +
                $"scene={start.sceneName}"
            );
        }
    }

    private async Task DispatchSnapshotWithSimulation(MatchSnapshot snapshot)
    {
        if (!simulateNetwork)
        {
            ApplySnapshot(snapshot);
            return;
        }

        if (UnityEngine.Random.value < incomingDropRate)
        {
            if (debugNetworkLog)
                Debug.LogWarning($"[{clientId}] [NETSIM] IN DROP tick={snapshot.tick}");

            return;
        }

        int delay = GetSimulatedDelay(incomingDelayMs, jitterMs);

        if (delay > 0)
        {
            if (debugNetworkLog)
                Debug.Log($"[{clientId}] [NETSIM] IN delay={delay}ms tick={snapshot.tick}");

            await Task.Delay(delay);
        }

        ApplySnapshot(snapshot);
    }

    private void ApplySnapshot(MatchSnapshot snapshot)
    {
        bool isOlder =
            snapshot.lastProcessedSeq < latestAppliedAck ||
            (snapshot.lastProcessedSeq == latestAppliedAck && snapshot.tick <= latestAppliedTick);

        if (isOlder)
        {
            if (debugNetworkLog)
            {
                Debug.LogWarning(
                    $"[{clientId}] DROP OLD SNAPSHOT tick={snapshot.tick} ack={snapshot.lastProcessedSeq} " +
                    $"latestTick={latestAppliedTick} latestAck={latestAppliedAck}"
                );
            }

            return;
        }

        latestAppliedAck = snapshot.lastProcessedSeq;
        latestAppliedTick = snapshot.tick;

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[{clientId}] APPLY SNAPSHOT tick={snapshot.tick} ack={snapshot.lastProcessedSeq} " +
                $"players={(snapshot.players != null ? snapshot.players.Length : 0)} " +
                $"projectiles={(snapshot.projectiles != null ? snapshot.projectiles.Length : 0)} " +
                $"events={(snapshot.events != null ? snapshot.events.Length : 0)} " +
                $"reject={snapshot.rejectReason}"
            );
        }

        ClientReceiver.Instance?.OnReceiveSnapshot(snapshot);
    }

    private int GetSimulatedDelay(int baseDelayMs, int jitterRangeMs)
    {
        int jitter = 0;

        if (jitterRangeMs > 0)
            jitter = UnityEngine.Random.Range(-jitterRangeMs, jitterRangeMs + 1);

        int finalDelay = baseDelayMs + jitter;
        return Mathf.Max(0, finalDelay);
    }

    private bool EnsureSocketOpen()
    {
        if (websocket == null || websocket.State != WebSocketState.Open)
        {
            Debug.LogWarning($"[{clientId}] WebSocket 未连接，当前状态不可发送。");
            return false;
        }

        return true;
    }

    private void Update()
    {
#if !UNITY_WEBGL || UNITY_EDITOR
        websocket?.DispatchMessageQueue();
#endif
    }

    private async void OnApplicationQuit()
    {
        applicationQuitting = true;

        if (debugNetworkLog)
        {
            Debug.Log(
                $"[RelayChatClient] OnApplicationQuit, disconnect socket. " +
                $"instance={GetInstanceID()}"
            );
        }

        await Disconnect();
    }
    public async Task LeaveAndDisconnect()
    {
        applicationQuitting = true;

        await Disconnect();

        if (Instance == this)
        {
            Instance = null;
        }

        Destroy(gameObject);
    }
    [ContextMenu("Connect")]
    private void ConnectFromMenu()
    {
        _ = Connect();
    }

    [ContextMenu("Create Room")]
    private void CreateRoomFromMenu()
    {
        _ = SendCreateRoom();
    }

    [ContextMenu("Leave Room")]
    private void LeaveFromMenu()
    {
        _ = LeaveRoom();
    }

    [ContextMenu("Disconnect")]
    private void DisconnectFromMenu()
    {
        _ = Disconnect();
    }
    private async void OnDestroy()
    {
        if (Instance == this)
        {
            Instance = null;
        }

        // 场景切换时不要主动断开 WebSocket。
        // 只有应用退出时才真正断线。
        if (!applicationQuitting)
        {
            if (debugNetworkLog)
            {
                Debug.Log(
                    $"[RelayChatClient] OnDestroy during scene change, keep socket if possible. " +
                    $"instance={GetInstanceID()}, scene={SceneManager.GetActiveScene().name}"
                );
            }

            return;
        }

        await Disconnect();
    }
}