using System.Collections.Concurrent;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Serialization;

namespace QuantAssistant.Api.Middleware;

/// <summary>
/// batch3 流 A：API 限流中间件。
/// 按 用户+IP 双维度固定窗口限流，默认 100 req/min（QUANT_RATE_LIMIT_PER_MIN）；
/// 超限返回 429（统一错误契约）+ Retry-After 头。
/// 登录/注册/恢复/改密接口走既有更严策略（Program.cs AddRateLimiter，20/min），此处跳过不叠加；
/// /internal/v1/worker/*（worker 心跳与领任务高频轮询）与非 /api/v1 请求不计入。
/// 计数纯内存实现（单机部署，无需 Redis）：应用重启清零；窗口滚动惰性清理过期键。
/// </summary>
public sealed class RateLimitMiddleware(RequestDelegate next, QuantOptions options, ILogger<RateLimitMiddleware> logger)
{
    private static readonly HashSet<string> StricterPaths = new(StringComparer.OrdinalIgnoreCase)
    {
        "/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/recovery/redeem", "/api/v1/auth/password", "/api/v1/auth/2fa/verify",
    };

    private sealed class Window
    {
        public long WindowStart;
        public int Count;
        public readonly Lock Sync = new();
    }

    private readonly ConcurrentDictionary<string, Window> _windows = new();

    public async Task InvokeAsync(HttpContext context)
    {
        var path = context.Request.Path.Value?.TrimEnd('/') ?? "";
        if (!path.StartsWith("/api/v1", StringComparison.OrdinalIgnoreCase) || StricterPaths.Contains(path))
        {
            await next(context);
            return;
        }
        var limit = options.RateLimitPerMin;
        var user = context.User.FindFirst("sub")?.Value ?? "anon";
        var ip = context.Connection.RemoteIpAddress?.ToString() ?? "unknown";
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var windowStart = now / 60 * 60;
        var window = _windows.GetOrAdd(user + "|" + ip, _ => new Window());
        int count;
        lock (window.Sync)
        {
            if (window.WindowStart != windowStart) { window.WindowStart = windowStart; window.Count = 0; }
            window.Count++;
            count = window.Count;
        }
        Prune(windowStart);
        if (count > limit)
        {
            context.Response.StatusCode = StatusCodes.Status429TooManyRequests;
            context.Response.Headers.RetryAfter = Math.Max(1, windowStart + 60 - now).ToString();
            context.Response.Headers["X-RateLimit-Limit"] = limit.ToString();
            context.Response.Headers["X-RateLimit-Remaining"] = "0";
            logger.LogWarning("API 限流 {User} {Ip} {Path}", user, ip, path);
            await context.Response.WriteAsJsonAsync(
                new ErrorResponse("rate_limited", "请求过于频繁，请稍后重试。", context.GetRequestId()), JsonDefaults.Options);
            return;
        }
        context.Response.Headers["X-RateLimit-Limit"] = limit.ToString();
        context.Response.Headers["X-RateLimit-Remaining"] = Math.Max(0, limit - count).ToString();
        await next(context);
    }

    private void Prune(long windowStart)
    {
        if (_windows.Count <= 20000) return;
        var cutoff = windowStart - 120;
        foreach (var pair in _windows)
            if (pair.Value.WindowStart < cutoff)
                _windows.TryRemove(pair.Key, out _);
    }
}
