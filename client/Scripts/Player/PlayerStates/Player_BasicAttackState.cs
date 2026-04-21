using UnityEngine;

public class Player_BasicAttackState :PlayerState
{
    public float attackVelocityTimer;
    private float lastTimeAttacked;

    private int attackDir;
    private int comboIndex = 1;
    private int comboLimit = 1;
    //private const int FirstComboIndex = 1;
    //private bool comboAttackQueued;
    private bool hasAttack =false;

    public Player_BasicAttackState(Player player, StateMachine stateMachine, string animBoolName) : base(player, stateMachine, animBoolName)
    {
        //if (comboLimit != player.attackVelocity.Length) comboLimit = player.attackVelocity.Length;
    }


    public override void Enter()
    {
        base.Enter();
        //ResetComboIndexIfNeeded();
        //SyncAttackSpeed();
        //comboAttackQueued = false;
        attackDir = player.moveInput.x != 0 ?((int) player.moveInput.x) : player.facingDir;
        //anim.SetInteger("basicAttackIndex",comboIndex);
        ApplyAttackVelocity();
        hasAttack = true;
     
    }

    public override void Update()
    {
        base.Update();
        HandleAttackVelocity();



        if (player.attackPressedThisFrame)
        {
            QueueNextAttack();
        }

        if (hasAttack)
        {
            HandleStateExit();

        }

    }

    public override void Exit()
    {
        base.Exit();
        //comboIndex++;

        lastTimeAttacked = Time.time;
    }
    private void HandleStateExit()
    {
        //if (comboAttackQueued)
        //{
        //    anim.SetBool(animBoolName, false);
        //    player.EnterAttackStateWithDelay();
        //}
         stateMachine.ChangeState(player.idleState);
    }

    private void QueueNextAttack()
    {
        //if (comboIndex < comboLimit)
        //{
        //    comboAttackQueued = true;
        //}
    }
    private void HandleAttackVelocity()
    {
        attackVelocityTimer -= Time.deltaTime;

        if (attackVelocityTimer < 0) player.SetVelocity(0, rb.linearVelocity.y);
    }

    private void ApplyAttackVelocity()
    {
        Vector2 attackVelocity = player.attackVelocity[comboIndex - 1];


        attackVelocityTimer = player.attackVelocityDuration;
        player.SetVelocity(attackVelocity.x * attackDir, attackVelocity.y);
    }

    private void ResetComboIndexIfNeeded()
    {

        //if (Time.time > lastTimeAttacked + player.comboResetTime)
        //{
        //    comboIndex = FirstComboIndex;
        //}


        //if (comboIndex > comboLimit)
        //{
        //    comboIndex = FirstComboIndex;
        //}
    }
}
