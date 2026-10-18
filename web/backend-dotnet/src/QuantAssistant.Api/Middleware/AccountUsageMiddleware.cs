using System.Diagnostics;
using QuantAssistant.Api.Services;

namespace QuantAssistant.Api.Middleware;

public sealed class AccountUsageMiddleware(RequestDelegate next)
{
    public async Task InvokeAsync(HttpContext context, UsageCounterService counters)
    {
        var started = Stopwatch.GetTimestamp();
        var failed=false;
        try { await next(context); }
        catch {failed=true;throw;}
        finally
        {
            var user = context.User.FindFirst("sub")?.Value;
            if (user is not null && context.Request.Path.StartsWithSegments("/api/v1"))
            {
                var category = context.Request.Path.Value!.Split('/', StringSplitOptions.RemoveEmptyEntries).ElementAtOrDefault(2) ?? "other";
                var duration = (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds;
                context.Response.OnCompleted(() =>
                {
                    counters.Observe(user, category, failed?500:context.Response.StatusCode, duration, DateTime.UtcNow);
                    return Task.CompletedTask;
                });
            }
        }
    }
}
