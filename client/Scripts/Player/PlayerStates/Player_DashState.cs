using UnityEngine;

public class Player_DashState : PlayerState
{
    public Player_DashState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    private int dashDir;
    private float originalGravityScale;

    public override void Enter()
    {
        base.Enter();

        if (player.useNetworkControl)
        {
            stateMachine.ChangeState(player.idleState);
            return;
        }

        player.SetVelocity(player.dashSpeed * player.facingDir, 0);
        dashDir = player.moveInput.x != 0 ? (int)player.moveInput.x : player.facingDir;
        stateTimer = player.dashDuration;
        originalGravityScale = rb.gravityScale;
        rb.gravityScale = 0;
    }

    public override void Update()
    {
        base.Update();

        if (player.useNetworkControl)
            return;

        cancelDashIfNeeded();
        player.SetVelocity(player.dashSpeed * dashDir, 0);

        if (stateTimer < 0)
        {
            if (player.groundDetected)
                stateMachine.ChangeState(player.idleState);
            else
                stateMachine.ChangeState(player.fallState);
        }
    }

    public override void Exit()
    {
        base.Exit();

        if (player.useNetworkControl)
            return;

        player.SetVelocity(0, 0);
        rb.gravityScale = originalGravityScale;
    }

    private void cancelDashIfNeeded()
    {
        if (player.wallDetected)
        {
            if (player.groundDetected)
                stateMachine.ChangeState(player.idleState);
            else
                stateMachine.ChangeState(player.wallSlideState);
        }
    }
}