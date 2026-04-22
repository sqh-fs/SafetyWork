using UnityEngine;

public class Player_FallState : Player_AirState
{
    public Player_FallState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Update()
    {
        base.Update();

        if (player.useNetworkControl)
        {
            if (player.GetIsGroundedForNet())
                stateMachine.ChangeState(player.idleState);

            return;
        }

        if (player.jumpPressedThisFrame && player.CanStartJump())
        {
            player.ConsumeJump();
            stateMachine.ChangeState(player.jumpState);
            return;
        }

        if (player.groundDetected)
            stateMachine.ChangeState(player.idleState);

        if (player.wallDetected)
            stateMachine.ChangeState(player.wallSlideState);
    }
}