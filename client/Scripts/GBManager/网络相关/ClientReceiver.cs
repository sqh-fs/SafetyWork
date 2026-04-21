using UnityEngine;

public class ClientReceiver : MonoBehaviour
{
    public static ClientReceiver Instance;

    [SerializeField] private Player player;
    [SerializeField] private bool debugSnapshotLog = true;

    private void Awake()
    {
        Instance = this;

        if (player == null)
            player = GetComponent<Player>();
    }

    public void OnReceiveSnapshot(MatchSnapshot snapshot)
    {
        if (snapshot == null)
            return;

        if (debugSnapshotLog)
        {
            Debug.Log(
                $"[ClientReceiver] ack={snapshot.lastProcessedSeq} " +
                $"state={snapshot.acceptedState} grounded={snapshot.acceptedGrounded} " +
                $"jumpCount={snapshot.acceptedJumpCount} reject={snapshot.rejectReason}"
            );
        }

        if (player == null)
            return;

        //player.ApplyServerState(
        //    snapshot.acceptedState,
        //    snapshot.acceptedGrounded,
        //    snapshot.acceptedJumpCount
        //);
    }
}