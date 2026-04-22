using UnityEngine;

public class Player_AirState : PlayerState
{
    public Player_AirState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Update()
    {
        base.Update();

        // 网络模式下，不再由 State 推动物理，只保留行为切换
        if (player.attackPressedThisFrame)
        {
            stateMachine.ChangeState(player.jumpAttackState);
            return;
        }

        // 二段跳的物理由 prediction controller 处理
        if (player.jumpPressedThisFrame && player.useNetworkControl)
        {
            // 不在这里手动改速度
            return;
        }

        if (!player.useNetworkControl)
        {
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
        }
    }
}