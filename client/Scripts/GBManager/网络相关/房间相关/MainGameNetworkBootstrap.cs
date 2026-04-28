using System;
using System.Threading.Tasks;
using UnityEngine;

public class MainGameNetworkBootstrap : MonoBehaviour
{
    [Header("引用")]
    [SerializeField] private RelayChatClient relayClient;

    [Header("调试")]
    [SerializeField] private bool showDebugOverlay = true;
    [SerializeField] private bool debugLog = true;

    [Header("连接等待")]
    [SerializeField] private float connectTimeoutSeconds = 5f;

    private string debugStatus = "Not started";
    private bool started;

    private async void Start()
    {
        if (started)
            return;

        started = true;

        debugStatus = "Start() entered";
        Log(debugStatus);

        await Task.Yield();

        await Bootstrap();
    }

    private async Task Bootstrap()
    {
        try
        {
            debugStatus = "Finding RelayChatClient singleton...";
            Log(debugStatus);

            if (relayClient == null)
                relayClient = RelayChatClient.Instance;

            if (relayClient == null)
                relayClient = FindFirstObjectByType<RelayChatClient>();

            if (relayClient == null)
            {
                debugStatus = "ERROR: RelayChatClient not found in MainGame.";
                Debug.LogError("[MainGameNetworkBootstrap] " + debugStatus);
                return;
            }

            if (NetworkSession.Instance == null)
            {
                debugStatus = "ERROR: NetworkSession.Instance is null.";
                Debug.LogError("[MainGameNetworkBootstrap] " + debugStatus);
                return;
            }

            string roomId = NetworkSession.Instance.roomId;
            string clientId = NetworkSession.Instance.clientId;
            string serverUrl = NetworkSession.Instance.serverUrl;

            debugStatus =
                $"Session room={roomId}, client={clientId}, server={serverUrl}, " +
                $"relayConnected={relayClient.IsConnected}, relayJoined={relayClient.HasJoinedRoom}";

            Log(debugStatus);

            if (string.IsNullOrWhiteSpace(roomId) || string.IsNullOrWhiteSpace(clientId))
            {
                debugStatus =
                    $"ERROR: Missing room/client. room={roomId}, client={clientId}";
                Debug.LogError("[MainGameNetworkBootstrap] " + debugStatus);
                return;
            }

            if (string.IsNullOrWhiteSpace(serverUrl))
            {
                serverUrl = "ws://127.0.0.1:8765";
                NetworkSession.Instance.serverUrl = serverUrl;
            }

            relayClient.ConfigureIdentity(
                serverUrl,
                clientId,
                roomId
            );

            if (!relayClient.IsConnected)
            {
                debugStatus = "Relay disconnected after scene load, reconnecting...";
                Log(debugStatus);

                await relayClient.Connect();

                bool connected = await WaitUntilConnected(connectTimeoutSeconds);

                if (!connected)
                {
                    debugStatus = "ERROR: reconnect timeout.";
                    Debug.LogError("[MainGameNetworkBootstrap] " + debugStatus);
                    return;
                }
            }

            bool alreadyJoinedSameRoom =
                relayClient.HasJoinedRoom &&
                string.Equals(relayClient.RoomId, roomId, StringComparison.OrdinalIgnoreCase) &&
                string.Equals(relayClient.ClientId, clientId, StringComparison.OrdinalIgnoreCase);

            if (!alreadyJoinedSameRoom)
            {
                debugStatus = $"Sending MainGame JOIN_ROOM client={clientId}, room={roomId}";
                Log(debugStatus);

                await relayClient.SendJoinRoomManual(clientId, roomId);
            }
            else
            {
                debugStatus = $"Already joined room={roomId} as {clientId}, skip JOIN.";
                Log(debugStatus);
            }

            debugStatus =
                $"Bootstrap done. connected={relayClient.IsConnected}, " +
                $"joined={relayClient.HasJoinedRoom}, client={relayClient.ClientId}, room={relayClient.RoomId}";

            Log(debugStatus);
        }
        catch (Exception ex)
        {
            debugStatus = "EXCEPTION: " + ex.Message;
            Debug.LogError("[MainGameNetworkBootstrap] Exception:\n" + ex);
        }
    }

    private async Task<bool> WaitUntilConnected(float timeoutSeconds)
    {
        float startTime = Time.realtimeSinceStartup;

        while (Time.realtimeSinceStartup - startTime < timeoutSeconds)
        {
            if (relayClient != null && relayClient.IsConnected)
                return true;

            debugStatus =
                $"Waiting connect... elapsed={(Time.realtimeSinceStartup - startTime):F1}s, " +
                $"connected={(relayClient != null && relayClient.IsConnected)}";

            await Task.Delay(50);
        }

        return relayClient != null && relayClient.IsConnected;
    }

    private void Log(string msg)
    {
        if (debugLog)
            Debug.Log("[MainGameNetworkBootstrap] " + msg);
    }

    private void OnGUI()
    {
        if (!showDebugOverlay)
            return;

        GUI.color = Color.white;

        string sessionInfo = "NetworkSession: null";

        if (NetworkSession.Instance != null)
        {
            sessionInfo =
                $"NetworkSession room={NetworkSession.Instance.roomId}\n" +
                $"NetworkSession client={NetworkSession.Instance.clientId}\n" +
                $"NetworkSession slot={NetworkSession.Instance.slotNo}\n" +
                $"NetworkSession host={NetworkSession.Instance.isHost}\n" +
                $"NetworkSession server={NetworkSession.Instance.serverUrl}";
        }

        string relayInfo = "RelayChatClient: null";

        if (relayClient != null)
        {
            relayInfo =
                $"Relay connected={relayClient.IsConnected}\n" +
                $"Relay joined={relayClient.HasJoinedRoom}\n" +
                $"Relay client={relayClient.ClientId}\n" +
                $"Relay room={relayClient.RoomId}";
        }

        GUI.Box(
            new Rect(10, 10, 560, 250),
            "MainGame Network Bootstrap\n\n" +
            $"Status: {debugStatus}\n\n" +
            sessionInfo + "\n\n" +
            relayInfo
        );
    }
}