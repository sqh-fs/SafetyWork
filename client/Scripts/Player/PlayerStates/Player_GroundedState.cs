using UnityEngine;

public class Player_GroundedState :PlayerState
{
    public Player_GroundedState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Enter()
    {
        base.Enter();
        player.ResetJumpCount();
    }

    public override void Update()
    {
        base.Update();

        if (rb.linearVelocity.y < 0 && player.groundDetected == false)
        {
            stateMachine.ChangeState(player.fallState);
        }

        if (player.jumpPressedThisFrame && player.CanStartJump())
        {
            player.ConsumeJump();
            stateMachine.ChangeState(player.jumpState);
            return;
        }

        if (player.attackPressedThisFrame)
            stateMachine.ChangeState(player.basicAttackState);
    }
}
