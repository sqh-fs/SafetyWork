using UnityEngine;

public class Player_WallSlideState : PlayerState
{
    public Player_WallSlideState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Enter()
    {
        base.Enter();


    }

    public override void Update()
    {
        base.Update();
        HandleWallSlide();

        if (player.jumpPressedThisFrame)
        {
            stateMachine.ChangeState(player.wallJumpState);

        }


        if (player.groundDetected)
        {
            stateMachine.ChangeState(player.idleState);
            if(player.facingDir != player.moveInput.x)
            {
                player.HandleFlip(player.facingDir);
            }
            


        }
        if(player.wallDetected == false)
        {
            stateMachine.ChangeState(player.fallState); 

        }

    }

    private void HandleWallSlide()
    {
        if(player.moveInput.y < 0)
        {
            player.SetVelocity(player.moveInput.x, rb.linearVelocity.y);

        }

        else
        {
            player.SetVelocity(player.moveInput.x, rb.linearVelocity.y *player.wallSlideSlowMultiplier);

        }

    }

}
