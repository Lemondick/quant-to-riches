using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/advisor")]
public sealed class AdvisorController(AdvisorExecutionService service, NativeScheduler scheduler) : ControllerBase
{
    public sealed record CreateBody(string Kind, bool Force = false);
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet("schedule")] public IActionResult Schedule() => Ok(service.Schedule(Id, scheduler.Running));
    [HttpGet("reports")] public IActionResult Reports(string? kind, [FromQuery(Name = "report_date")] DateOnly? date, int page = 1, int limit = 20) => Ok(service.List(Id, kind, date, page, limit));
    [HttpGet("reports/{report_id}")] public IActionResult Report(string report_id) => Ok(service.Get(Id, report_id));
    [HttpPost("reports")] public IActionResult Create(CreateBody body) => Accepted(service.Create(Id, body.Kind, body.Force));
    [HttpPost("preflight")] public async Task<IActionResult> Preflight(CreateBody body, CancellationToken cancellation) => Ok(await service.Preflight(Id, body.Kind, cancellation));

    [HttpGet("notifications")]
    public async Task<IActionResult> Notifications(CancellationToken cancellation = default) =>
        Ok(new JsonObject { ["items"] = await service.NotificationsAsync(Id, cancellation) });

    [HttpPost("notifications/{notice_id}/acknowledge")]
    public async Task<IActionResult> Acknowledge(string notice_id, CancellationToken cancellation = default)
    {
        var ok = await service.AcknowledgeNotificationAsync(Id, notice_id, cancellation);
        return ok ? Ok(new { status = "acknowledged" }) : NotFound(new { error_code = "notice_not_found", message = "未找到通知" });
    }
}
