using UnityEngine;

public class Player_IdleState : Player_GroundedState
{
    public Player_IdleState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Enter()
    {
        base.Enter();

        if (!player.useNetworkControl)
            player.SetVelocity(0, rb.linearVelocity.y);
    }

    public override void Update()
    {
        base.Update();

        if (player.moveInput.x != 0)
        {
            stateMachine.ChangeState(player.moveState);
            return;
        }
    }

    public override void Exit()
    {
        base.Exit();
    }
}