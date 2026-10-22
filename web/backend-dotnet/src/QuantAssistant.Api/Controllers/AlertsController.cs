using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/alerts")]
public sealed class AlertCenterController(AlertCenterRepository alerts, ComputeService compute) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet("center/settings")]
    public async Task<IActionResult> GetSettings(CancellationToken cancellation = default) => Ok(await alerts.GetSettingsAsync(Id, cancellation));

    [HttpPut("center/settings")]
    public async Task<IActionResult> UpdateSettings([FromBody] AlertSettings body, CancellationToken cancellation = default)
    {
        if (body.StaleAfterDays is < 1 or > 365 || body.DrawdownThreshold is < 0.001 or > 0.9 || body.TaskFailureDays is < 1 or > 365)
            throw new ArgumentException("告警阈值无效");
        return Ok(await alerts.SaveSettingsAsync(Id, body, cancellation));
    }

    [HttpGet("center")]
    public async Task<IActionResult> Read(CancellationToken cancellation = default)
    {
        JsonObject dates;
        try
        {
            dates = await compute.Run(Id, "analysis.price_dates", new { }, cancellation, accountSnapshot: true) as JsonObject ?? new();
        }
        catch (ApplicationFailure) { dates = new(); } // Missing evidence stays unknown when the worker is unavailable.
        return Ok(await alerts.EvaluateAsync(Id, dates, cancellation));
    }

    [HttpPost("center/{id}/acknowledge")]
    public async Task<IActionResult> Acknowledge(string id, CancellationToken cancellation = default)
    {
        var changed = await alerts.AcknowledgeAsync(Id, id, cancellation);
        return changed == 0 ? NotFound(new { error_code = "alert_not_found", message = "未找到告警" }) : Ok(new { status = "acknowledged" });
    }
}
