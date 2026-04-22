using System;

[Serializable]
public struct PredictedPlayerState
{
    public float posX;
    public float posY;   // ÓïÒå£º½Åµ× y
    public float velX;
    public float velY;

    public bool grounded;
    public int jumpCount;
    public bool acceptedDrop;
    public string stateName;
}