using System;

[Serializable]
public class MatchSnapshot
{
    public int tick;
    public int lastProcessedSeq;

    public string acceptedState;
    public bool acceptedGrounded;
    public int acceptedJumpCount;

    public string rejectReason;
}