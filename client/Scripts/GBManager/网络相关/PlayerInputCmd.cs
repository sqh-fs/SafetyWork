using System;

[Serializable]
public class PlayerInputCmd
{
    public int seq;

    public float moveX;
    public bool jumpPressed;
    public bool attackPressed;

    public float aimX;
    public float aimY;

    // 客户端本地状态摘要
    public string clientState;
    public bool clientGrounded;
    public int clientJumpCount;
}