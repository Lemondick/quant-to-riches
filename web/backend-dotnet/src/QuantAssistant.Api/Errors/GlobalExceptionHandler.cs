using Microsoft.AspNetCore.Diagnostics;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Serialization;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Errors;

/// <summary>
/// 全局异常处理器：统一输出 {"error_code","message","request_id"}，不泄漏内部细节。
/// </summary>
public sealed class GlobalExceptionHandler(ILogger<GlobalExceptionHandler> logger) : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(
        HttpContext httpContext,
        Exception exception,
        CancellationToken cancellationToken)
    {
        var (status, code) = ExceptionTranslator.Translate(exception);
        if (status >= 500)
        {
            // 仅对服务器内部错误记录完整堆栈；业务错误不落日志避免噪音。
            logger.LogError(exception, "Unhandled exception mapped to {Code}", code);
        }

        var requestId = httpContext.GetRequestId();
        var message = exception is ApplicationFailure failure ? failure.Message
            : exception is AuthFailure or AccountNotFound ? AuthErrors.Message(code) : ErrorMessages.Get(code);
        var payload = new ErrorResponse(code, message, requestId);

        httpContext.Response.StatusCode = status;
        if (code == "public_snapshot_unavailable") httpContext.Response.Headers.RetryAfter = "2";
        httpContext.Response.ContentType = "application/json; charset=utf-8";
        await httpContext.Response.WriteAsJsonAsync(payload, JsonDefaults.Options, cancellationToken);
        return true;
    }
}
