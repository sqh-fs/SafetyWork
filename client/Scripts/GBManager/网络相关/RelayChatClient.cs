using System;
using System.Text;
using System.Threading.Tasks;
using NativeWebSocket;
using UnityEngine;

/// <summary>
/// 最小可运行 Unity WebSocket 客户端（用于“客户端 -> 服务端 -> 另一客户端”的中转演示）
///
/// 依赖库：
/// - NativeWebSocket
///
/// 本脚本支持：
/// 1. 连接服务器
/// 2. 发送 JOIN_ROOM
/// 3. 发送 CHAT
/// 4. 接收 SERVER_BROADCAST / ERROR
/// 5. 发送 LEAVE_ROOM
/// </summary>
public class RelayChatClient : MonoBehaviour
{
    [Header("连接配置")]
    [SerializeField] private string serverUrl = "ws://127.0.0.1:8765";
    [SerializeField] private string clientId = "Client1";
    [SerializeField] private string roomId = "demo-room";
    [SerializeField] private bool autoConnect = true;
    [SerializeField] private bool autoJoin = true;
    [SerializeField] private bool debugNetworkLog = true;
    [SerializeField] private int logEveryNInputs = 10;

    private WebSocket websocket;
    private bool hasJoinedRoom;
    private int sentInputCount;

    /// <summary>
    /// 统一消息结构（与服务端 JSON 对应）
    /// 字段尽量覆盖所有消息类型，未使用字段可为空。
    /// </summary>
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
    }

    /// <summary>
    /// 连接 WebSocket 服务端。
    /// </summary>
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

    /// <summary>
    /// 向服务端发送 JOIN_ROOM。
    /// </summary>
    private async Task SendJoinRoom()
    {
        if (!EnsureSocketOpen())
        {
            return;
        }

        var join = new NetMessage
        {
            type = "JOIN_ROOM",
            roomId = roomId,
            clientId = clientId
        };

        await SendJson(join);
        hasJoinedRoom = true;
        Debug.Log($"[{clientId}] 已发送 JOIN_ROOM，room={roomId}");
    }

    /// <summary>
    /// 发送聊天消息到服务端，服务端再转发到另一个客户端。
    /// </summary>
    public async Task SendChat(string messageText)
    {
        if (string.IsNullOrWhiteSpace(messageText))
        {
            Debug.LogWarning($"[{clientId}] CHAT 内容为空，已忽略。");
            return;
        }

        if (!EnsureSocketOpen())
        {
            return;
        }

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

        await SendJson(chat);
        Debug.Log($"[{clientId}] 已发送 CHAT -> Server: {messageText}");
    }

    /// <summary>
    /// 离开房间（发送 LEAVE_ROOM）。
    /// </summary>
    public async Task LeaveRoom()
    {
        if (!EnsureSocketOpen())
        {
            return;
        }

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

        await SendJson(leave);
        hasJoinedRoom = false;
        Debug.Log($"[{clientId}] 已发送 LEAVE_ROOM，room={roomId}");
    }

    /// <summary>
    /// 主动断开 WebSocket 连接。
    /// </summary>
    public async Task Disconnect()
    {
        if (websocket == null)
        {
            return;
        }

        if (websocket.State == WebSocketState.Open || websocket.State == WebSocketState.Connecting)
        {
            await websocket.Close();
        }

        websocket = null;
        hasJoinedRoom = false;
        Debug.Log($"[{clientId}] 已执行 Disconnect。");
    }

    /// <summary>
    /// 统一 JSON 发送入口。
    /// </summary>
    /// 
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

        await SendJson(msg);
    }

    private async Task SendJson(NetMessage message)
    {
        if (websocket == null)
        {
            Debug.LogWarning($"[{clientId}] websocket 为空，发送失败。");
            return;
        }

        string json = JsonUtility.ToJson(message);
        await websocket.SendText(json);
    }

    /// <summary>
    /// 处理服务端发回的消息，并打印关键日志用于演示。
    /// </summary>
    private void HandleIncomingMessage(string json)
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
                var snapshot = JsonUtility.FromJson<MatchSnapshot>(msg.payload);
                if (debugNetworkLog && snapshot != null)
                {
                    Debug.Log(
                        $"[{clientId}] APPLY SNAPSHOT tick={snapshot.tick} " +
                        $"ack={snapshot.lastProcessedSeq} state={snapshot.acceptedState} " +
                        $"grounded={snapshot.acceptedGrounded} jumpCount={snapshot.acceptedJumpCount}"
                    );
                }
                ClientReceiver.Instance?.OnReceiveSnapshot(snapshot);
                break;

            case "ERROR":
                Debug.LogError($"[{clientId}] SERVER ERROR: {msg.error}");
                break;
        }
    }


    /// <summary>
    /// 判断 WebSocket 是否处于 Open 状态。
    /// </summary>
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
        // NativeWebSocket 官方建议：非 WebGL 平台在 Update 中主动调度消息队列。
#if !UNITY_WEBGL || UNITY_EDITOR
        websocket?.DispatchMessageQueue();
#endif
    }

    private async void OnApplicationQuit()
    {
        await Disconnect();
    }

    // -----------------------
    // 下面几个 ContextMenu 仅用于快速演示（可选）
    // -----------------------

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
