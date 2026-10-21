using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/plans")]
public sealed class PlanningController(CashFlowPlanRepository plans) : ControllerBase
{
    public sealed record CashFlowPlanBody(string Name, string Direction, decimal Amount, string Frequency, DateOnly StartDate, DateOnly? EndDate = null, string? Note = null, string? PlanId = null, string? Status = null, DateTimeOffset? UpdatedAt = null);
    public sealed record SetStatusBody(string Status, DateTimeOffset ExpectedUpdatedAt);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static readonly string[] Directions = ["in", "out"];
    private static readonly string[] Frequencies = ["once", "weekly", "monthly", "quarterly", "annual"];
    private static readonly string[] Statuses = ["active", "paused", "archived"];
    private static void Text(string value, int maximum, string message)
    { if (string.IsNullOrWhiteSpace(value) || value.Length > maximum) throw new ArgumentException(message); }
    private static void OptionalText(string? value, int maximum, string message)
    { if (value is not null && (string.IsNullOrWhiteSpace(value) || value.Length > maximum)) throw new ArgumentException(message); }
    private static void Limit(int limit, int minimum, int maximum, string message)
    { if (limit < minimum || limit > maximum) throw new ArgumentException(message); }
    private static void Validate(CashFlowPlanBody body)
    {
        Text(body.Name, 200, "计划名称无效"); OptionalText(body.Note, 2000, "备注无效");
        if (!Directions.Contains(body.Direction)) throw new ArgumentException("资金方向无效");
        if (body.Amount <= 0 || body.Amount >= 1000000000000m) throw new ArgumentException("金额无效");
        if (!Frequencies.Contains(body.Frequency)) throw new ArgumentException("频率无效");
        if (body.EndDate is not null && body.EndDate <= body.StartDate) throw new ArgumentException("结束日期须晚于开始日期");
        if (body.Frequency != "once" && body.EndDate is not null && body.StartDate.AddYears(20) < body.EndDate) throw new ArgumentException("计划周期过长");
        if (body.Status is not null && !Statuses.Contains(body.Status)) throw new ArgumentException("计划状态无效");
    }
    private static string Normalize(string? value) => (value ?? string.Empty).Trim();

    [HttpGet]
    public async Task<IActionResult> List([FromQuery] string? status = null, [FromQuery] int limit = 100, [FromQuery] int offset = 0, CancellationToken cancellation = default)
    {
        if (status is not null && !Statuses.Contains(status)) throw new ArgumentException("状态无效");
        Limit(limit, 1, 500, "条数无效"); Limit(offset, 0, 100000, "偏移无效");
        return Ok(await plans.ListAsync(UserId, status, limit, offset, cancellation));
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CashFlowPlanBody body, CancellationToken cancellation = default)
    {
        Validate(body);
        var id = Normalize(body.PlanId); if (id.Length > 128) throw new ArgumentException("计划编号无效");
        return Ok(await plans.CreateAsync(UserId, Normalize(body.Name), body.Direction, body.Amount, body.Frequency,
            body.StartDate, body.EndDate, Normalize(body.Note), id.Length == 0 ? null : id, cancellation));
    }

    [HttpPut("{id}")]
    public async Task<IActionResult> Update(string id, [FromBody] CashFlowPlanBody body, CancellationToken cancellation = default)
    {
        Validate(body);
        return Ok(await plans.UpdateAsync(UserId, id, Normalize(body.Name), body.Direction, body.Amount, body.Frequency,
            body.StartDate, body.EndDate, Normalize(body.Note), body.Status ?? "active",
            body.UpdatedAt ?? throw new ArgumentException("缺少更新时间"), cancellation));
    }

    [HttpPost("{id}/status")]
    public async Task<IActionResult> SetStatus(string id, [FromBody] SetStatusBody body, CancellationToken cancellation = default)
    {
        if (!Statuses.Contains(body.Status)) throw new ArgumentException("状态无效");
        return Ok(await plans.SetStatusAsync(UserId, id, body.Status, body.ExpectedUpdatedAt, cancellation));
    }

    [HttpGet("due")]
    public async Task<IActionResult> Due([FromQuery(Name = "as_of")] DateOnly? asOf = null, CancellationToken cancellation = default) =>
        Ok(await plans.DueAsync(UserId, asOf ?? DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)), cancellation));
}
