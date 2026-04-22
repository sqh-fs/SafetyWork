using System;

[Serializable]
public class MatchSnapshot
{
    public int tick;
    public int lastProcessedSeq;

    public string acceptedState;
    public bool acceptedGrounded;
    public int acceptedJumpCount;
    public bool acceptedDrop;

    public float serverPosX;
    public float serverPosY;
    public float serverVelX;
    public float serverVelY;

    public string rejectReason;
}