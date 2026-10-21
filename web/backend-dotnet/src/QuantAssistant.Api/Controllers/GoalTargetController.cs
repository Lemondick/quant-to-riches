using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch5 任务1：目标规划后端（api/v1/goals）。
/// CRUD + 目标投影（POST {id}/project：current_amount 取账本净资产，透调 worker goal.project）。
/// 写操作走 user_operation_audit（F6）；只测算、不执行真实交易。
/// </summary>
[ApiController]
[Route("api/v1/goals")]
public sealed class GoalTargetController(GoalTargetRepository repo, GoalProjectionService projection,
    UserOperationAuditWriter audit, QuantOptions options) : ControllerBase
{
    public sealed record CreateBody(string Name, decimal TargetAmount, DateOnly TargetDate,
        double? AssumedAnnualReturn = null, decimal? CurrentAmount = null);
    public sealed record UpdateBody(string? Name = null, decimal? TargetAmount = null,
        DateOnly? TargetDate = null, double? AssumedAnnualReturn = null);
    public sealed record ProjectBody(decimal? MonthlyAmount = null);

    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static JsonNode? Clone(JsonObject node) => JsonNode.Parse(node.ToJsonString());

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateBody body, CancellationToken cancellation = default)
    {
        var userId = Id;
        JsonObject goal;
        try
        {
            // 缺省 current_amount 取账本净资产；缺省假设年化取配置（QUANT_GOAL_DEFAULT_ANNUAL_RETURN）。
            var current = body.CurrentAmount ?? await projection.LedgerNetWorthAsync(userId, cancellation);
            goal = await repo.CreateAsync(userId, body.Name, body.TargetAmount, body.TargetDate, current,
                body.AssumedAnnualReturn ?? options.GoalDefaultAnnualReturn, cancellation);
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
        await audit.RecordAsync(userId, "goal_target_created", "goal_target", goal["id"]!.GetValue<string>(), null, Clone(goal), cancellation);
        return Created($"/api/v1/goals/{goal["id"]!.GetValue<string>()}", goal);
    }

    [HttpGet]
    public async Task<IActionResult> List(CancellationToken cancellation = default) =>
        Ok(new { items = await repo.ListAsync(Id, cancellation) });

    [HttpGet("{id}")]
    public async Task<IActionResult> Get(string id, CancellationToken cancellation = default) =>
        Ok(await repo.GetAsync(Id, id, cancellation));

    [HttpPut("{id}")]
    public async Task<IActionResult> Update(string id, [FromBody] UpdateBody body, CancellationToken cancellation = default)
    {
        var userId = Id;
        var before = await repo.GetAsync(userId, id, cancellation);
        JsonObject goal;
        try
        {
            goal = await repo.UpdateAsync(userId, id, body.Name, body.TargetAmount, body.TargetDate,
                body.AssumedAnnualReturn, cancellation);
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
        await audit.RecordAsync(userId, "goal_target_updated", "goal_target", id, Clone(before), Clone(goal), cancellation);
        return Ok(goal);
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(string id, CancellationToken cancellation = default)
    {
        var userId = Id;
        var before = await repo.GetAsync(userId, id, cancellation);
        await repo.DeleteAsync(userId, id, cancellation);
        await audit.RecordAsync(userId, "goal_target_deleted", "goal_target", id, Clone(before), null, cancellation);
        return Ok(new { status = "deleted" });
    }

    [HttpPost("{id}/project")]
    public async Task<IActionResult> Project(string id, [FromBody] ProjectBody? body, CancellationToken cancellation = default)
    {
        var userId = Id;
        try
        {
            return Ok(await projection.ProjectAsync(userId, id, body?.MonthlyAmount, cancellation));
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
    }
}
