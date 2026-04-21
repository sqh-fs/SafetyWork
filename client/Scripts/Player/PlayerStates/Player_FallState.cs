using UnityEngine;

public class Player_FallState : Player_AirState
{
    public Player_FallState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Enter()
    {
        base.Enter();
    }

    public override void Update()
    {
        base.Update();

        if (player.jumpPressedThisFrame && player.CanStartJump())
        {
            player.ConsumeJump();
            stateMachine.ChangeState(player.jumpState);
            return;
        }

        if (player.groundDetected == true)
            stateMachine.ChangeState(player.idleState);

        if (player.wallDetected == true)
            stateMachine.ChangeState(player.wallSlideState);
    }
}