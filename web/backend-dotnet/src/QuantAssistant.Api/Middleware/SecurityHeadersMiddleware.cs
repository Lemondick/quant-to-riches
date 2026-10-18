namespace QuantAssistant.Api.Middleware;

/// <summary>
/// 安全响应头，与旧站点 app.py 一致：
/// X-Content-Type-Options: nosniff / X-Frame-Options: DENY / Referrer-Policy: no-referrer / Cache-Control: no-store。
/// </summary>
public sealed class SecurityHeadersMiddleware(RequestDelegate next)
{
    public async Task InvokeAsync(HttpContext context)
    {
        context.Response.OnStarting(() =>
        {
            var headers = context.Response.Headers;
            headers["X-Content-Type-Options"] = "nosniff";
            headers["X-Frame-Options"] = "DENY";
            headers["Referrer-Policy"] = "no-referrer";
            headers["Cache-Control"] = "no-store";
            return Task.CompletedTask;
        });

        await next(context);
    }
}
