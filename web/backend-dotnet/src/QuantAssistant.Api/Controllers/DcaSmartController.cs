using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务5：智能定投后端（估值驱动）。
/// 计划 CRUD（api/v1/dca/smart）+ 回测（POST {id}/backtest 透调 worker op dca.smart_plan_backtest）。
/// 执行提醒由 Scheduler 每日检查（到期/估值触发），只提醒、不自动扣款。
/// </summary>
[ApiController]
[Route("api/v1/dca/smart")]
public sealed class DcaSmartController(SmartDcaRepository repo, SmartDcaService service, UserOperationAuditWriter audit) : ControllerBase
{
    public sealed record CreateBody(string Symbol, decimal BaseAmount, JsonObject Rules, string Frequency = "monthly", DateOnly? NextRunDate = null);
    public sealed record UpdateBody(JsonObject? Rules = null, string? Frequency = null, string? Status = null, DateOnly? NextRunDate = null);
    public sealed record BacktestBody(DateOnly Start, DateOnly End);

    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static JsonNode? Clone(JsonObject node) => JsonNode.Parse(node.ToJsonString());

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateBody body, CancellationToken cancellation = default)
    {
        var userId = Id;
        JsonObject plan;
        try
        {
            plan = await repo.CreateAsync(userId, body.Symbol, body.BaseAmount, body.Rules, body.Frequency, body.NextRunDate, cancellation);
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
        await audit.RecordAsync(userId, "smart_dca_plan_created", "smart_dca_plan", plan["id"]!.GetValue<string>(), null, Clone(plan), cancellation);
        return Created($"/api/v1/dca/smart/{plan["id"]!.GetValue<string>()}", plan);
    }

    [HttpGet]
    public async Task<IActionResult> List([FromQuery] string? status = null, CancellationToken cancellation = default) =>
        Ok(new { items = await repo.ListAsync(Id, status, cancellation) });

    [HttpGet("{id}")]
    public async Task<IActionResult> Get(string id, CancellationToken cancellation = default) =>
        Ok(await repo.GetAsync(Id, id, cancellation));

    [HttpPut("{id}")]
    public async Task<IActionResult> Update(string id, [FromBody] UpdateBody body, CancellationToken cancellation = default)
    {
        var userId = Id;
        var before = await repo.GetAsync(userId, id, cancellation);
        JsonObject plan;
        try
        {
            plan = await repo.UpdateAsync(userId, id, body.Rules, body.Frequency, body.Status, body.NextRunDate, cancellation);
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
        await audit.RecordAsync(userId, "smart_dca_plan_updated", "smart_dca_plan", id, Clone(before), Clone(plan), cancellation);
        return Ok(plan);
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(string id, CancellationToken cancellation = default)
    {
        var userId = Id;
        var before = await repo.GetAsync(userId, id, cancellation);
        await repo.DeleteAsync(userId, id, cancellation);
        await audit.RecordAsync(userId, "smart_dca_plan_deleted", "smart_dca_plan", id, Clone(before), null, cancellation);
        return Ok(new { status = "deleted" });
    }

    [HttpPost("{id}/backtest")]
    public async Task<IActionResult> Backtest(string id, [FromBody] BacktestBody body, CancellationToken cancellation = default)
    {
        try
        {
            var result = await service.BacktestAsync(Id, id, body.Start, body.End, cancellation);
            return Ok(new { result });
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
    }
}
