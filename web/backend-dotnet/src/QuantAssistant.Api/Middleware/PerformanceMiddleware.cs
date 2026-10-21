using System.Diagnostics;
using System.Globalization;
using Microsoft.AspNetCore.Routing;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using Serilog.Context;

namespace QuantAssistant.Api.Middleware;

public sealed class PerformanceMiddleware(RequestDelegate next, ILogger<PerformanceMiddleware> logger, SlowRequestAggregator slowRequests)
{
    public async Task InvokeAsync(HttpContext context)
    {
        var supplied = context.Request.Headers["X-Trace-Id"].FirstOrDefault();
        var traceId = supplied is { Length: 32 } && supplied.All(char.IsAsciiHexDigit)
            ? supplied.ToLowerInvariant() : Activity.Current?.TraceId.ToHexString() ?? Guid.NewGuid().ToString("N");
        var previous = PerformanceTrace.Current;
        var trace = new PerformanceTrace(traceId);
        PerformanceTrace.Current = trace;
        var began = Stopwatch.GetTimestamp();
        using var logContext = LogContext.PushProperty("TraceId", traceId);
        context.Response.OnStarting(() =>
        {
            trace.Record("gateway", Stopwatch.GetElapsedTime(began).TotalMilliseconds);
            context.Response.Headers["X-Trace-Id"] = traceId;
            context.Response.Headers.Append("Server-Timing", string.Join(", ", trace.Timings.Select(pair => $"{pair.Key};dur={pair.Value.ToString("F3", CultureInfo.InvariantCulture)}")));
            return Task.CompletedTask;
        });
        try { await next(context); }
        finally
        {
            var elapsed = Stopwatch.GetElapsedTime(began).TotalMilliseconds;
            if (elapsed > 500 && !(context.Request.Path == "/internal/v1/worker/claim" && context.Response.StatusCode == 204))
            {
                logger.LogWarning("Slow request {TraceId} {Path} {ElapsedMs}ms {@Timings}", traceId, context.Request.Path.Value, elapsed, trace.Timings);
                // batch3-A：慢请求进聚合器（按小时桶 endpoint+次数+p95，见 SlowQueryAlertService）。
                var endpoint = (context.GetEndpoint() as RouteEndpoint)?.RoutePattern.RawText ?? context.Request.Path.Value;
                slowRequests.Record(SlowRequestAggregator.NormalizeEndpoint(endpoint), elapsed);
            }
            PerformanceTrace.Current = previous;
        }
    }
}
