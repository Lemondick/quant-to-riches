using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Research;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/research/funds/execution-replays")]
public sealed partial class FundExecutionReplayController(FundExecutionReplayRepository replays, ComputeService compute) : ControllerBase
{
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    public sealed record ReplayBody(JsonObject Bundle, string? RequestId = null);
    [GeneratedRegex("^[a-f0-9]{32}$")] private static partial Regex Identifier();

    [HttpPost, RequestSizeLimit(2 * 1024 * 1024)]
    public async Task<IActionResult> Create(ReplayBody body, CancellationToken cancellation)
    {
        if (body.Bundle is null || Encoding.UTF8.GetByteCount(body.Bundle.ToJsonString()) > 2 * 1024 * 1024) throw new ArgumentException("证据包过大或为空");
        if (body.Bundle["symbol"] is not JsonValue symbol || !symbol.TryGetValue<string>(out var symbolText)) throw new ArgumentException("请填写字符串基金代码");
        FundFeeRepository.Symbol(symbolText);
        var id = body.RequestId ?? Guid.NewGuid().ToString("N");
        if (!Identifier().IsMatch(id)) throw new ArgumentException("回放请求编号无效");
        var existing = await replays.FindExistingAsync(UserId, id, body.Bundle, checkQuota: true, cancellation);
        if (existing is not null) return Ok(existing);
        var result = (await compute.Run(UserId, "fund.execution_replay", new { bundle = body.Bundle }, cancellation))!.AsObject();
        result["research_only"] = true;
        result["qualifies_strategy"] = false;
        result["qualifies_paper_days"] = false;
        result["ready_for_trading"] = false;
        var record = new JsonObject { ["id"] = id, ["created_at"] = DateTimeOffset.UtcNow.ToString("O"), ["bundle"] = body.Bundle.DeepClone(), ["result"] = result };
        record["fingerprint"] = FundExecutionReplayRepository.Fingerprint(record);
        var committed = await replays.CommitAsync(UserId, id, body.Bundle, record, cancellation);
        return ReferenceEquals(committed, record) ? Created("/api/v1/research/funds/execution-replays/" + id, record) : Ok(committed);
    }

    [HttpGet]
    public async Task<IActionResult> List(CancellationToken cancellation) => Ok(await replays.ListAsync(UserId, cancellation));

    private async Task<JsonObject> ReadAsync(string id, CancellationToken cancellation)
    {
        if (!Identifier().IsMatch(id)) throw new ApplicationFailure("replay_not_found", 404, "未找到执行回放。");
        return await replays.ReadAsync(UserId, id, cancellation);
    }

    [HttpGet("{id}")] public async Task<IActionResult> Get(string id, CancellationToken cancellation) => Ok(await ReadAsync(id, cancellation));
    [HttpGet("{id}/export")] public async Task<IActionResult> Export(string id, CancellationToken cancellation) =>
        File(Encoding.UTF8.GetBytes((await ReadAsync(id, cancellation)).ToJsonString()), "application/json; charset=utf-8", "fund-execution-replay-" + id + ".json");

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(string id, CancellationToken cancellation)
    {
        if (!Identifier().IsMatch(id)) throw new ApplicationFailure("replay_not_found", 404, "未找到执行回放。");
        var changed = await replays.DeleteAsync(UserId, id, cancellation);
        return changed == 0 ? NotFound(new { error_code = "replay_not_found", message = "未找到执行回放。" }) : Ok(new { status = "deleted" });
    }
}
