using UnityEngine;

public class ClientReceiver : MonoBehaviour
{
    public static ClientReceiver Instance;

    [SerializeField] private ClientPredictionController predictionController;
    [SerializeField] private Player player;
    [SerializeField] private bool debugSnapshotLog = true;

    [Header("ต๗สิ")]
    [SerializeField] private bool forceSnapToLatestServerSnapshot = false;

    private MatchSnapshot latestSnapshot;

    private void Awake()
    {
        Instance = this;

        if (predictionController == null)
            predictionController = GetComponent<ClientPredictionController>();

        if (player == null)
            player = GetComponent<Player>();

        Debug.Log($"[ClientReceiver] Awake on {gameObject.name}, prediction={(predictionController != null ? predictionController.name : "null")}");
    }

    public void OnReceiveSnapshot(MatchSnapshot snapshot)
    {
        if (snapshot == null)
            return;

        latestSnapshot = snapshot;

        if (debugSnapshotLog)
        {
            Debug.Log(
                $"[ClientReceiver] ack={snapshot.lastProcessedSeq} " +
                $"state={snapshot.acceptedState} grounded={snapshot.acceptedGrounded} " +
                $"jumpCount={snapshot.acceptedJumpCount} drop={snapshot.acceptedDrop} " +
                $"pos=({snapshot.serverPosX}, {snapshot.serverPosY}) " +
                $"vel=({snapshot.serverVelX}, {snapshot.serverVelY}) " +
                $"reject={snapshot.rejectReason}"
            );
        }

        if (forceSnapToLatestServerSnapshot)
        {
            ForceSnapToLatestServerSnapshot();
            return;
        }

        predictionController?.Reconcile(snapshot);
    }

    [ContextMenu("Force Snap To Latest Server Snapshot")]
    public void ForceSnapToLatestServerSnapshot()
    {
        if (latestSnapshot == null || player == null)
            return;

        Debug.Log($"[ClientReceiver] FORCE SNAP -> serverPos=({latestSnapshot.serverPosX:F3}, {latestSnapshot.serverPosY:F3})");

        player.ApplyServerPosition(
            latestSnapshot.serverPosX,
            latestSnapshot.serverPosY,
            latestSnapshot.serverVelY
        );

        player.ApplyServerState(
            latestSnapshot.acceptedState,
            latestSnapshot.acceptedGrounded,
            latestSnapshot.acceptedJumpCount
        );
    }
}