using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/admin/calendar")]
public sealed class CalendarController(TradingCalendarRepository calendars, ComputeService compute) : ControllerBase
{
    public sealed record SaveBody(int Year, string Source, string[] Holidays, string? ExpectedRevision = null, string Market = "CN");
    public sealed record AutoGenerateBody(string Market = "CN", int? Year = null);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet]
    public IActionResult Read([FromQuery] int? year = null, [FromQuery] string market = "CN") => Ok(calendars.View(year ?? DateTime.UtcNow.AddHours(8).Year, market));
    [HttpGet("status")]
    public IActionResult Status([FromQuery] string market = "CN") => Ok(calendars.Status(DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)), market));
    [HttpPut]
    public IActionResult Save([FromBody] SaveBody body) => Ok(calendars.Save(UserId, new(body.Year, body.Source, body.Holidays, body.Market), body.ExpectedRevision));

    /// <summary>F1：管理员触发 worker 自动生成次年（或指定年份）交易日历，origin=auto、待确认。</summary>
    [HttpPost("auto-generate")]
    public async Task<IActionResult> AutoGenerate([FromBody] AutoGenerateBody body, CancellationToken cancellation)
    {
        var year = body.Year ?? DateTime.UtcNow.AddHours(8).Year + 1;
        var market = body.Market;
        var result = (await compute.Run(UserId, "calendar.generate_next_year", new { market, year }, cancellation))?.AsObject()
            ?? throw new ApplicationFailure("worker_unavailable", 503, "日历生成未返回结果，请检查计算进程后重试。");
        // 处理 {status, error_code} 信封（B 侧未落地或失败时）。
        if (result["status"]?.GetValue<string>() is string status && status != "ok" || result["error_code"] is not null)
            throw new ApplicationFailure(result["error_code"]?.GetValue<string>() ?? "calendar_generate_failed", 502,
                result["message"]?.GetValue<string>() ?? "自动生成交易日历失败。");
        var holidays = result["holidays"]?.AsArray().Select(h => h!.GetValue<string>()).ToArray()
            ?? throw new ApplicationFailure("calendar_generate_failed", 502, "自动生成的日历缺少休市日期。");
        var view = calendars.SaveAuto(UserId, market, year, holidays,
            result["source"]?.GetValue<string>() ?? "worker:calendar.generate_next_year",
            result["confidence"]?.GetValue<double>(),
            result["notes"]?.AsArray().Select(n => n!.GetValue<string>()).ToArray(),
            new JsonObject { ["op"] = "calendar.generate_next_year", ["at"] = DateTimeOffset.UtcNow.ToString("O"), ["by"] = UserId });
        return Ok(view);
    }

    /// <summary>F1：管理员确认自动生成的日历。</summary>
    [HttpPost("{market}/{year}/confirm")]
    public IActionResult Confirm(string market, int year) => Ok(calendars.Confirm(UserId, market, year));
}
