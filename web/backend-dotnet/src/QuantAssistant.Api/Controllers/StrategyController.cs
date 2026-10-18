using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/research/strategy-settings")]
public sealed class StrategyController(AccountDatabase database, ComputeService compute) : ControllerBase
{
    private string Id => User.FindFirst("sub")!.Value;
    private async Task<JsonObject> Current(CancellationToken cancellation) => (await database.QueryAsync("SELECT payload FROM user_documents WHERE user_id=$1 AND document_key='strategy-settings'", [Id], cancellation)).FirstOrDefault()?["payload"]!.AsObject() ?? new() { ["parameters"] = new JsonObject(), ["updated_at"] = null };

    [HttpGet]
    public async Task<IActionResult> Read(CancellationToken cancellation) => Ok(await compute.Run(Id, "strategy.view", await Current(cancellation), cancellation));

    [HttpPut]
    public async Task<IActionResult> Save(JsonObject body, CancellationToken cancellation)
    {
        var result = (await compute.Run(Id, "strategy.view", new { parameters = body, updated_at = DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(8)).ToString("O") }, cancellation))!.AsObject();
        await database.WriteAsync(Id, sql => sql.ExecuteAsync("INSERT INTO user_documents(user_id,document_key,revision,payload,updated_at) VALUES($1,'strategy-settings',1,$2::jsonb,now()) ON CONFLICT(user_id,document_key) DO UPDATE SET revision=user_documents.revision+1,payload=EXCLUDED.payload,updated_at=now()", Id, result.ToJsonString()), cancellation);
        return Ok(result);
    }

    [HttpPost("preview")]
    public async Task<IActionResult> Preview(JsonObject body, CancellationToken cancellation)
    {
        var result = (await compute.Run(Id, "strategy.preview", new { parameters = body }, cancellation, accountSnapshot: true))!.AsObject();
        if (result.Remove("_evidence", out var sealedEvidence) && sealedEvidence is JsonObject evidence)
            await database.WriteAsync(Id, sql => sql.ExecuteAsync("INSERT INTO agent_preview_snapshots(user_id,snapshot_id,content_hash,payload_json,created_at,expires_at) VALUES($1,$2,$3,$4::json,$5,$6) ON CONFLICT(user_id,snapshot_id) DO NOTHING", Id,
                result["preview_snapshot_id"]!.GetValue<string>(), evidence["content_hash"]!.GetValue<string>(), evidence["payload"]!.ToJsonString(), DateTimeOffset.Parse(evidence["created_at"]!.GetValue<string>()).UtcDateTime, DateTimeOffset.Parse(evidence["expires_at"]!.GetValue<string>()).UtcDateTime), cancellation);
        return Ok(result);
    }
}
