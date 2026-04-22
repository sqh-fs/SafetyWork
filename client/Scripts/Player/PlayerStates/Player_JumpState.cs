using UnityEngine;

public class Player_JumpState : Player_AirState
{
    public Player_JumpState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Enter()
    {
        base.Enter();

        if (!player.useNetworkControl)
            player.SetVelocity(rb.linearVelocity.x, player.jumpForce);
    }

    public override void Update()
    {
        base.Update();

        if (player.useNetworkControl)
        {
            float vy = player.GetCurrentVelY();

            if (vy < 0f)
                stateMachine.ChangeState(player.fallState);

            return;
        }

        if (rb.linearVelocity.y < 0)
            stateMachine.ChangeState(player.fallState);
    }
}