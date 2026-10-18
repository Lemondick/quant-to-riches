namespace QuantAssistant.Core.Errors;

/// <summary>Fixed application-domain failure; never use provider or SQL text as its message.</summary>
public sealed class ApplicationFailure(string code, int status, string message) : BusinessError(code, message)
{
    public int Status { get; } = status;
}
