using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/portfolio/drawdown")]
public sealed class DrawdownController(AccountDatabase database) : ControllerBase
{
    public sealed record NavPoint(DateOnly? AsOfDate = null, double? UnitNav = null, double? TotalValue = null);
    public sealed record Body(string RunId, NavPoint[] Nav, double Threshold = 0.2);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private const string Columns = "run_id,threshold,max_drawdown,observed_sessions,breach_count,passed,reasons_json AS reasons,research_only";
    [HttpGet]
    public IActionResult Read([FromQuery(Name = "run_id")] string? runId = null)
    {
        if (runId is not null && (string.IsNullOrWhiteSpace(runId) || runId.Length > 128)) throw new ArgumentException("运行ID无效");
        return Ok(database.Read(sql => sql.Query($"SELECT {Columns} FROM drawdown_validations WHERE user_id=$1 AND ($2::text IS NULL OR run_id=$2) ORDER BY validated_at DESC,run_id DESC LIMIT 1", UserId, runId).FirstOrDefault()));
    }
    [HttpPost("validate")]
    public IActionResult Validate([FromBody] Body body)
    {
        if (string.IsNullOrWhiteSpace(body.RunId) || body.RunId.Length > 128 || body.Nav.Length is < 2 or > 10000 || body.Nav.Any(p => p is null)
            || !double.IsFinite(body.Threshold) || body.Threshold is <= 0 or > 1) throw new ArgumentException("回撤验证参数无效");
        var reasons = new List<string>(); var values = new List<double>(); DateOnly? previous = null;
        var dated = body.Nav.Any(p => p.AsOfDate is not null);
        foreach (var point in body.Nav)
        {
            if (dated)
            {
                if (point.AsOfDate is null) reasons.Add("invalid_nav_date");
                else { if (previous is not null && point.AsOfDate <= previous) reasons.Add("nav_dates_not_strictly_increasing"); previous = point.AsOfDate; }
            }
            var value = point.UnitNav ?? point.TotalValue;
            if (value is not { } number || !double.IsFinite(number) || number <= 0) reasons.Add("invalid_nav_value"); else values.Add(number);
        }
        if (values.Count < 2) reasons.Add("insufficient_nav_sessions");
        var maximumValue = 0.0; var maxDrawdown = 0.0; var breaches = 0;
        foreach (var value in values) { maximumValue = Math.Max(maximumValue, value); var drawdown = Math.Max(0, 1 - value / maximumValue); maxDrawdown = Math.Max(maxDrawdown, drawdown); if (drawdown > body.Threshold) breaches++; }
        if (breaches > 0) reasons.Add("drawdown_control_line_breached");
        var report = new JsonObject { ["run_id"] = body.RunId.Trim(), ["threshold"] = body.Threshold, ["max_drawdown"] = maxDrawdown,
            ["observed_sessions"] = values.Count, ["breach_count"] = breaches, ["passed"] = reasons.Count == 0,
            ["reasons"] = JsonSerializer.SerializeToNode(reasons.Distinct().ToArray()), ["research_only"] = true };
        return Ok(database.Write(UserId, sql =>
        {
            var existing = sql.Query($"SELECT {Columns} FROM drawdown_validations WHERE user_id=$1 AND run_id=$2", UserId, body.RunId.Trim()).FirstOrDefault();
            if (existing is not null) { if (!JsonNode.DeepEquals(existing, report)) throw new ArgumentException("相同运行ID已有不同回撤结果"); return existing; }
            sql.Execute("INSERT INTO drawdown_validations(user_id,run_id,threshold,max_drawdown,observed_sessions,breach_count,passed,research_only,reasons_json,validated_at) VALUES($1,$2,$3,$4,$5,$6,$7,true,$8::json,now())", UserId, body.RunId.Trim(), body.Threshold, maxDrawdown, values.Count, breaches, reasons.Count == 0, report["reasons"]!.ToJsonString());
            return report;
        }));
    }
}
