using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Operations;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/operations")]
public sealed class OperationsViewsController(JobHeartbeatRepository heartbeats) : ControllerBase
{
    public sealed record HeartbeatBody(string JobName, int ExpectedIntervalMinutes, string? Status = null, DateTime? StartedAt = null, DateTime? CompletedAt = null, string? Error = null);
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpPost("heartbeats")]
    public async Task<IActionResult> Heartbeat([FromBody] HeartbeatBody body, CancellationToken cancellation = default)
    {
        if (string.IsNullOrWhiteSpace(body.JobName) || body.JobName.Length > 200) throw new ArgumentException("任务名无效");
        if (body.ExpectedIntervalMinutes is < 1 or > 43200) throw new ArgumentException("预期心跳间隔无效");
        if (body.Status is not null && body.Status is not ("healthy" or "failed" or "blocked")) throw new ArgumentException("心跳状态无效");
        if (body.Error is not null && body.Error.Length > 2000) throw new ArgumentException("心跳错误信息过长");
        await heartbeats.RecordHeartbeatAsync(Id, body.JobName, body.ExpectedIntervalMinutes, body.Status ?? "healthy", body.StartedAt, body.CompletedAt, body.Error, cancellation);
        return Ok(new { status = "ok" });
    }

    [HttpGet("watchdog")]
    public async Task<IActionResult> Watchdog(CancellationToken cancellation = default) =>
        Ok(await heartbeats.WatchdogAsync(Id, cancellation));

    [HttpGet("trends")]
    public async Task<IActionResult> Trends([FromQuery] int limit = 365, CancellationToken cancellation = default)
    {
        if (limit is < 1 or > 730) throw new ArgumentException("条数无效");
        return Ok(await heartbeats.TrendsAsync(Id, limit, cancellation));
    }
}
