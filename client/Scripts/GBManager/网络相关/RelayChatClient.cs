using System;
using System.Text;
using System.Threading.Tasks;
using NativeWebSocket;
using UnityEngine;

/// <summary>
/// 最小可运行 Unity WebSocket 客户端（带网络模拟器）
/// 增加：按 ack/tick 丢弃旧快照
/// </summary>
public class RelayChatClient : MonoBehaviour
{
    [Header("连接配置")]
    [SerializeField] private string serverUrl = "ws://127.0.0.1:8765";
    [SerializeField] private string clientId = "Client1";
    [SerializeField] private string roomId = "demo-room";
    [SerializeField] private bool autoConnect = true;
    [SerializeField] private bool autoJoin = true;

    [Header("调试")]
    [SerializeField] private bool debugNetworkLog = true;
    [SerializeField] private int logEveryNInputs = 10;

    [Header("Network Simulation")]
    [SerializeField] private bool simulateNetwork = false;   // 先关掉排查
    [SerializeField] private int outgoingDelayMs = 120;
    [SerializeField] private int incomingDelayMs = 120;
    [SerializeField] private int jitterMs = 20;
    [SerializeField, Range(0f, 1f)] private float outgoingDropRate = 0f;
    [SerializeField, Range(0f, 1f)] private float incomingDropRate = 0f;

    private WebSocket websocket;
    private bool hasJoinedRoom;
    private int sentInputCount;

    // 关键：客户端只接受更新的快照
    private int latestAppliedAck = -1;
    private int latestAppliedTick = -1;

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

    private void Start()
    {
        if (autoConnect)
        {
            _ = Connect();
        }
        Debug.Log($"[RelayChatClient] Start on {gameObject.name}, clientId={clientId}");
    }

    public async Task Connect()
    {
        if (websocket != null &&
            (websocket.State == WebSocketState.Open || websocket.State == WebSocketState.Connecting))
        {
            Debug.LogWarning($"[{clientId}] WebSocket 已连接或正在连接中。");
            return;
        }

        websocket = new WebSocket(serverUrl);

        websocket.OnOpen += () =>
        {
            Debug.Log($"[{clientId}] 已连接到服务端: {serverUrl}");
            if (autoJoin)
            {
                _ = SendJoinRoom();
            }
        };

        websocket.OnError += (errorMsg) =>
        {
            Debug.LogError($"[{clientId}] WebSocket 错误: {errorMsg}");
        };

        websocket.OnClose += (closeCode) =>
        {
            Debug.LogWarning($"[{clientId}] 连接已关闭，code={closeCode}");
            hasJoinedRoom = false;
        };

        websocket.OnMessage += (bytes) =>
        {
            string text = Encoding.UTF8.GetString(bytes);
            HandleIncomingMessage(text);
        };

        Debug.Log($"[{clientId}] 正在连接: {serverUrl}");
        await websocket.Connect();
    }

    private async Task SendJoinRoom()
    {
        if (!EnsureSocketOpen())
            return;

        var join = new NetMessage
        {
            type = "JOIN_ROOM",
            roomId = roomId,
            clientId = clientId
        };

        await SendJson(join, bypassSimulation: false);
        hasJoinedRoom = true;
        Debug.Log($"[{clientId}] 已发送 JOIN_ROOM，room={roomId}");
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

        var chat = new NetMessage
        {
            type = "CHAT",
            roomId = roomId,
            clientId = clientId,
            text = messageText
        };

        await SendJson(chat, bypassSimulation: false);
        Debug.Log($"[{clientId}] 已发送 CHAT -> Server: {messageText}");
    }

    public async Task LeaveRoom()
    {
        if (!EnsureSocketOpen())
            return;

        if (!hasJoinedRoom)
        {
            Debug.LogWarning($"[{clientId}] 当前不在房间中，无需 LEAVE_ROOM。");
            return;
        }

        var leave = new NetMessage
        {
            type = "LEAVE_ROOM",
            roomId = roomId,
            clientId = clientId
        };

        await SendJson(leave, bypassSimulation: false);
        hasJoinedRoom = false;
        Debug.Log($"[{clientId}] 已发送 LEAVE_ROOM，room={roomId}");
    }

    public async Task Disconnect()
    {
        if (websocket == null)
            return;

        if (websocket.State == WebSocketState.Open || websocket.State == WebSocketState.Connecting)
        {
            await websocket.Close();
        }

        websocket = null;
        hasJoinedRoom = false;
        Debug.Log($"[{clientId}] 已执行 Disconnect。");
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
        if (debugNetworkLog && (sentInputCount == 1 || sentInputCount % Mathf.Max(1, logEveryNInputs) == 0))
        {
            Debug.Log($"[{clientId}] SEND INPUT #{sentInputCount} seq={cmd.seq} payload={msg.payload}");
        }

        await SendJson(msg, bypassSimulation: false);
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
            {
                Debug.LogWarning($"[{clientId}] [NETSIM] OUT DROP type={message.type}");
            }
            return;
        }

        int delay = GetSimulatedDelay(outgoingDelayMs, jitterMs);
        if (delay > 0)
        {
            if (debugNetworkLog)
            {
                Debug.Log($"[{clientId}] [NETSIM] OUT delay={delay}ms type={message.type}");
            }
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
        {
            Debug.Log($"[{clientId}] RECV RAW {json}");
        }

        var msg = JsonUtility.FromJson<NetMessage>(json);
        if (msg == null || string.IsNullOrEmpty(msg.type))
            return;

        switch (msg.type)
        {
            case "SNAPSHOT":
                {
                    var snapshot = JsonUtility.FromJson<MatchSnapshot>(msg.payload);
                    if (snapshot == null)
                        return;

                    if (debugNetworkLog)
                    {
                        Debug.Log(
                            $"[{clientId}] SNAPSHOT RAW tick={snapshot.tick} " +
                            $"ack={snapshot.lastProcessedSeq} state={snapshot.acceptedState} " +
                            $"grounded={snapshot.acceptedGrounded} jumpCount={snapshot.acceptedJumpCount} " +
                            $"drop={snapshot.acceptedDrop} pos=({snapshot.serverPosX}, {snapshot.serverPosY}) " +
                            $"vel=({snapshot.serverVelX}, {snapshot.serverVelY})"
                        );
                    }

                    await DispatchSnapshotWithSimulation(snapshot);
                    break;
                }

            case "SERVER_BROADCAST":
                {
                    if (debugNetworkLog)
                    {
                        Debug.Log($"[{clientId}] SERVER_BROADCAST from={msg.fromClientId} text={msg.text}");
                    }
                    break;
                }

            case "ERROR":
                {
                    Debug.LogError($"[{clientId}] SERVER ERROR: {msg.error}");
                    break;
                }
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
            {
                Debug.LogWarning($"[{clientId}] [NETSIM] IN DROP tick={snapshot.tick}");
            }
            return;
        }

        int delay = GetSimulatedDelay(incomingDelayMs, jitterMs);
        if (delay > 0)
        {
            if (debugNetworkLog)
            {
                Debug.Log($"[{clientId}] [NETSIM] IN delay={delay}ms tick={snapshot.tick}");
            }
            await Task.Delay(delay);
        }

        ApplySnapshot(snapshot);
    }

    private void ApplySnapshot(MatchSnapshot snapshot)
    {
        // 关键：按 ack/tick 丢弃旧快照
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
                $"[{clientId}] APPLY SNAPSHOT tick={snapshot.tick} " +
                $"ack={snapshot.lastProcessedSeq} state={snapshot.acceptedState} " +
                $"grounded={snapshot.acceptedGrounded} jumpCount={snapshot.acceptedJumpCount} " +
                $"drop={snapshot.acceptedDrop} pos=({snapshot.serverPosX}, {snapshot.serverPosY}) " +
                $"vel=({snapshot.serverVelX}, {snapshot.serverVelY})"
            );
        }

        ClientReceiver.Instance?.OnReceiveSnapshot(snapshot);
    }

    private int GetSimulatedDelay(int baseDelayMs, int jitterRangeMs)
    {
        int jitter = 0;
        if (jitterRangeMs > 0)
        {
            jitter = UnityEngine.Random.Range(-jitterRangeMs, jitterRangeMs + 1);
        }

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
        await Disconnect();
    }

    [ContextMenu("Connect")]
    private void ConnectFromMenu()
    {
        _ = Connect();
    }

    [ContextMenu("Send Test Chat")]
    private void SendTestChatFromMenu()
    {
        _ = SendChat($"Hello from {clientId} @ {DateTime.Now:HH:mm:ss}");
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
}