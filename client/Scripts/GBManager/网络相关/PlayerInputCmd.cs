using System;

[Serializable]
public class PlayerInputCmd
{
    public int seq;

    public float moveX;
    public bool jumpPressed;
    public bool attackPressed;
    public bool downHeld;
    public bool dropPressed;

    public float aimX;
    public float aimY;

    // 客户端本地状态摘要
    public string clientState;
    public bool clientGrounded;
    public int clientJumpCount;

    // 调试用：只做对比，不参与服务器权威裁决
    public float clientPosX;
    public float clientVelX;
}