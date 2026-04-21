using UnityEngine;

public class Player_AirState : PlayerState
{
    public Player_AirState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }


    public override void Update()
    {
        base.Update();

        if (player.moveInput.x != 0)
        {
            player.SetVelocity(player.moveSpeed * player.moveInput.x * player.inAirMoveMultiplier, rb.linearVelocity.y);
        }

        if (player.jumpPressedThisFrame && player.CanStartJump())
        {
            player.ConsumeJump();

            stateMachine.ChangeState(player.jumpState);
            return;
        }

        if (player.attackPressedThisFrame)
        {
            stateMachine.ChangeState(player.jumpAttackState);
        }
    }
}
