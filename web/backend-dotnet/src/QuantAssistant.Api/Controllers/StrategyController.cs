using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Research;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/research/strategy-settings")]
public sealed class StrategyController(StrategyRepository strategies, ComputeService compute) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet]
    public async Task<IActionResult> Read(CancellationToken cancellation) =>
        Ok(await compute.Run(Id, "strategy.view", await strategies.GetSettingsAsync(Id, cancellation), cancellation));

    [HttpPut]
    public async Task<IActionResult> Save(JsonObject body, CancellationToken cancellation)
    {
        var result = (await compute.Run(Id, "strategy.view", new { parameters = body, updated_at = DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(8)).ToString("O") }, cancellation))!.AsObject();
        await strategies.SaveSettingsAsync(Id, result, cancellation);
        return Ok(result);
    }

    [HttpPost("preview")]
    public async Task<IActionResult> Preview(JsonObject body, CancellationToken cancellation)
    {
        var result = (await compute.Run(Id, "strategy.preview", new { parameters = body }, cancellation, accountSnapshot: true))!.AsObject();
        if (result.Remove("_evidence", out var sealedEvidence) && sealedEvidence is JsonObject evidence)
            await strategies.SavePreviewSnapshotAsync(Id,
                result["preview_snapshot_id"]!.GetValue<string>(), evidence["content_hash"]!.GetValue<string>(), evidence["payload"]!.ToJsonString(),
                DateTimeOffset.Parse(evidence["created_at"]!.GetValue<string>()).UtcDateTime, DateTimeOffset.Parse(evidence["expires_at"]!.GetValue<string>()).UtcDateTime, cancellation);
        return Ok(result);
    }
}
