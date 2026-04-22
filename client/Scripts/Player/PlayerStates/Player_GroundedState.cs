using UnityEngine;

public class Player_GroundedState : PlayerState
{
    public Player_GroundedState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Enter()
    {
        base.Enter();

        if (!player.useNetworkControl)
            player.ResetJumpCount();
    }

    public override void Update()
    {
        base.Update();

        if (player.jumpPressedThisFrame)
        {
            stateMachine.ChangeState(player.jumpState);
            return;
        }

        if (player.attackPressedThisFrame)
        {
            stateMachine.ChangeState(player.basicAttackState);
            return;
        }

        if (!player.useNetworkControl)
        {
            if (rb.linearVelocity.y < 0 && player.groundDetected == false)
            {
                stateMachine.ChangeState(player.fallState);
                return;
            }

            if (player.CanDropThroughOneWay())
            {
                player.TryDropThroughOneWay();
                stateMachine.ChangeState(player.fallState);
                return;
            }
        }
    }
}