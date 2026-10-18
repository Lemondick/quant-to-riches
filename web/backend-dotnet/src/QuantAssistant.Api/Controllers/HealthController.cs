using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Serialization;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Api.Services;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// 健康端点（/healthz /readyz /metrics），与旧站点一致且不进 OpenAPI schema。
/// 就绪检查在配置了原生 worker 时要求当前心跳，重启后的旧心跳不能替代就绪。
/// </summary>
[ApiController]
[ApiExplorerSettings(IgnoreApi = true)]
public sealed class HealthController(IReadinessProbe probe,NativeMetrics metrics,IConfiguration configuration,AccountDatabase database,NativeOperationsMonitor monitor) : ControllerBase
{
    private static readonly string Version = "1.0";

    [HttpGet("/healthz")]
    public IActionResult Health()
    {
        var health = probe.Check();
        var available = health.Status == "ok" && health.Database == "connected";
        if (available)
        {
            return Ok(new { status = "ok", version = Version });
        }

        return StatusCode(StatusCodes.Status503ServiceUnavailable, new { status = "degraded", version = Version });
    }

    [HttpGet("/readyz")]
    public IActionResult Ready()
    {
        var health = probe.Check();
        var available = health.Status == "ok" && health.Database == "connected";
        if (available && configuration["QUANT_WORKER_SECRET"] is { Length: >= 32 })
            available = database.Read(sql => monitor.Workers(sql)["status"]!.GetValue<string>() == "healthy");
        if (available)
        {
            return Ok(new { status = "ready" });
        }

        return StatusCode(StatusCodes.Status503ServiceUnavailable, new { status = "not_ready" });
    }

    [HttpGet("/metrics")]
    public IActionResult Metrics([FromServices] UsageCounterService counters)
    {
        return Content(metrics.Render() + $"# TYPE quant_usage_counter_dropped_total counter\nquant_usage_counter_dropped_total {counters.Dropped}\n", "text/plain; version=0.0.4");
    }
}
