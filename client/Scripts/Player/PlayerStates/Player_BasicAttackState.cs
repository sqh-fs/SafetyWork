using UnityEngine;

public class Player_BasicAttackState : PlayerState
{
    public float attackVelocityTimer;
    private float lastTimeAttacked;

    private int attackDir;
    private int comboIndex = 1;
    private int comboLimit = 1;
    private bool hasAttack = false;

    public Player_BasicAttackState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
    }

    public override void Enter()
    {
        base.Enter();

        attackDir = player.moveInput.x != 0 ? (int)player.moveInput.x : player.facingDir;

        if (!player.useNetworkControl)
            ApplyAttackVelocity();

        hasAttack = true;
    }

    public override void Update()
    {
        base.Update();

        if (!player.useNetworkControl)
            HandleAttackVelocity();

        if (player.attackPressedThisFrame)
            QueueNextAttack();

        if (hasAttack)
            HandleStateExit();
    }

    public override void Exit()
    {
        base.Exit();
        lastTimeAttacked = Time.time;
    }

    private void HandleStateExit()
    {
        stateMachine.ChangeState(player.idleState);
    }

    private void QueueNextAttack()
    {
    }

    private void HandleAttackVelocity()
    {
        attackVelocityTimer -= Time.deltaTime;
        if (attackVelocityTimer < 0)
            player.SetVelocity(0, rb.linearVelocity.y);
    }

    private void ApplyAttackVelocity()
    {
        Vector2 attackVelocity = player.attackVelocity[comboIndex - 1];
        attackVelocityTimer = player.attackVelocityDuration;
        player.SetVelocity(attackVelocity.x * attackDir, attackVelocity.y);
    }
}