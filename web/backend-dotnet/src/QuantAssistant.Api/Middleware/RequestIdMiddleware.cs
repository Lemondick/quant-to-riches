namespace QuantAssistant.Api.Middleware;

/// <summary>
/// 请求 ID 中间件：沿用入站 X-Request-ID 或生成新的，回写 X-Request-ID 响应头。
/// 与旧站点 app.py 的 add_request_id_and_security 行为一致。
/// </summary>
public sealed class RequestIdMiddleware(RequestDelegate next)
{
    public async Task InvokeAsync(HttpContext context)
    {
        var requestId = context.Request.Headers["X-Request-ID"].FirstOrDefault();
        if (string.IsNullOrWhiteSpace(requestId))
        {
            requestId = Guid.NewGuid().ToString("N");
        }

        context.SetRequestId(requestId);
        context.Response.OnStarting(() =>
        {
            context.Response.Headers["X-Request-ID"] = requestId;
            return Task.CompletedTask;
        });

        await next(context);
    }
}
