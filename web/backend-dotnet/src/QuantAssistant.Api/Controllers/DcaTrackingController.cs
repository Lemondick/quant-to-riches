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
/// batch5 任务4：定投执行跟踪（api/v1/dca）。
/// GET /tracking（计划 vs 执行：按期/漏投/金额偏离统计）、
/// POST /executions（登记一次实际扣款）、POST /executions/{...}/skip（标记跳过）。
/// 登记写操作走 user_operation_audit（F6）；只记录与提醒，不自动扣款。
/// </summary>
[ApiController]
[Route("api/v1/dca")]
public sealed class DcaTrackingController(DcaExecutionRepository repo, DcaExecutionTrackingService tracking,
    UserOperationAuditWriter audit) : ControllerBase
{
    public sealed record RecordBody(string PlanId, DateOnly DueDate, decimal ActualAmount,
        DateTimeOffset? ExecutedAt = null, string? Note = null);
    public sealed record SkipBody(string PlanId, DateOnly DueDate, string? Note = null);

    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static JsonNode? Clone(JsonObject node) => JsonNode.Parse(node.ToJsonString());

    [HttpGet("tracking")]
    public async Task<IActionResult> Tracking(CancellationToken cancellation = default) =>
        Ok(await tracking.TrackingAsync(Id, cancellation));

    [HttpPost("executions")]
    public async Task<IActionResult> Record([FromBody] RecordBody body, CancellationToken cancellation = default)
    {
        var userId = Id;
        JsonObject execution;
        try
        {
            execution = await repo.RecordAsync(userId, body.PlanId, body.DueDate, body.ActualAmount, body.ExecutedAt, body.Note, cancellation);
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
        await audit.RecordAsync(userId, "dca_execution_recorded", "dca_execution", execution["id"]!.GetValue<string>(), null, Clone(execution), cancellation);
        return Created($"/api/v1/dca/executions/{execution["id"]!.GetValue<string>()}", execution);
    }

    [HttpPost("executions/skip")]
    public async Task<IActionResult> Skip([FromBody] SkipBody body, CancellationToken cancellation = default)
    {
        var userId = Id;
        var execution = await repo.SkipAsync(userId, body.PlanId, body.DueDate, body.Note, cancellation);
        await audit.RecordAsync(userId, "dca_execution_skipped", "dca_execution", execution["id"]!.GetValue<string>(), null, Clone(execution), cancellation);
        return Ok(execution);
    }
}
