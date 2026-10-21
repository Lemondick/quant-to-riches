using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务1：组合再平衡后端。
/// 目标配置 CRUD（api/v1/rebalance/targets）+ 偏离评估（api/v1/rebalance/evaluate）。
/// 评估只给出"卖出X买入Y"调仓建议，绝不下单（automatic_trading=false 随行声明）。
/// </summary>
[ApiController]
[Route("api/v1/rebalance")]
public sealed class RebalanceController(RebalanceRepository repo, UserOperationAuditWriter audit) : ControllerBase
{
    public sealed record TargetsBody(decimal StockRatio, decimal BondRatio, decimal CashRatio,
        decimal IndustryConcentrationLimit = 0.30m, decimal DeviationThreshold = 0.05m);

    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static JsonNode? Clone(JsonObject? node) => node is null ? null : JsonNode.Parse(node.ToJsonString());

    [HttpGet("targets")]
    public async Task<IActionResult> GetTargets(CancellationToken cancellation = default) =>
        Ok(new { targets = await repo.GetAsync(Id, cancellation) });

    [HttpPut("targets")]
    public async Task<IActionResult> SaveTargets([FromBody] TargetsBody body, CancellationToken cancellation = default)
    {
        var userId = Id;
        var before = await repo.GetAsync(userId, cancellation);
        JsonObject saved;
        try
        {
            saved = await repo.SaveAsync(userId, body.StockRatio, body.BondRatio, body.CashRatio,
                body.IndustryConcentrationLimit, body.DeviationThreshold, cancellation);
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
        await audit.RecordAsync(userId, "rebalance_targets_saved", "rebalance_targets", userId,
            Clone(before), Clone(saved), cancellation);
        return Ok(new { targets = saved });
    }

    [HttpPost("evaluate")]
    public async Task<IActionResult> Evaluate(CancellationToken cancellation = default)
    {
        var userId = Id;
        var result = await repo.EvaluateAsync(userId, cancellation);
        await audit.RecordAsync(userId, "rebalance_evaluated", "rebalance_targets", userId, null,
            Clone(result), cancellation);
        return Ok(result);
    }
}
