using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

public sealed record AlertSettings(long Revision = 0, int StaleAfterDays = 7, double DrawdownThreshold = .1, int TaskFailureDays = 7);

[ApiController, Route("api/v1/alerts")]
public sealed class AlertsController(AccountDatabase database, FundFeeRepository fees, ComputeService compute) : ControllerBase
{
    private static readonly JsonSerializerOptions Options = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private string Id => User.FindFirst("sub")!.Value;
    private static AlertSettings Settings(AccountSql sql, string user) => sql.Scalar("SELECT payload::text FROM alert_center_settings WHERE user_id=$1 AND id=1", user) is string raw ? JsonSerializer.Deserialize<AlertSettings>(raw, Options)! : new();

    [HttpGet("settings")]
    public IActionResult ReadSettings() => Ok(database.Read(sql => Settings(sql, Id)));

    [HttpPut("settings")]
    public IActionResult SaveSettings(AlertSettings body)
    {
        if (body.Revision < 0 || body.StaleAfterDays is < 0 or > 365 || !double.IsFinite(body.DrawdownThreshold) || body.DrawdownThreshold is <= 0 or > 1 || body.TaskFailureDays is < 1 or > 90) throw new ArgumentException("告警阈值无效");
        return Ok(database.WriteNonLedger(Id, sql =>
        {
            var current = Settings(sql, Id);
            if (current.Revision != body.Revision) throw new ApplicationFailure("alert_settings_conflict", 409, "告警阈值已变化，请重新读取。");
            body = body with { Revision = current.Revision + 1 };
            sql.Execute("INSERT INTO alert_center_settings(user_id,id,payload) VALUES($1,1,$2::json) ON CONFLICT(user_id,id) DO UPDATE SET payload=EXCLUDED.payload", Id, JsonSerializer.Serialize(body, Options));
            return body;
        }));
    }

    [HttpPost("{alert_id}/acknowledge")]
    public IActionResult Acknowledge(string alert_id) => database.WriteNonLedger(Id, sql => sql.Execute("UPDATE user_alert_center SET acknowledged=true WHERE user_id=$1 AND id=$2", Id, alert_id)) == 1
        ? Ok(new { status = "acknowledged" }) : NotFound(new { error_code = "alert_not_found", message = "未找到当前账户告警" });

    [HttpGet]
    public async Task<IActionResult> Read(CancellationToken cancellation)
    {
        JsonObject priceDates;
        try { priceDates = (await compute.Run(Id, "analysis.price_dates", new { }, cancellation, accountSnapshot: true))!.AsObject(); }
        catch (ApplicationFailure) { priceDates = new(); } // Missing evidence raises an alert; it never implies fresh data.
        return Ok(database.WriteNonLedger(Id, sql =>
        {
            var settings = Settings(sql, Id); var today = FundFeeRepository.Today(); var found = new List<(string Kind, string Subject, JsonObject Detail)>();
            void Add(string kind, string subject, string message, JsonObject? detail = null)
            { detail ??= new(); detail["message"] = message; found.Add((kind, subject, detail)); }
            var rows = sql.Query("SELECT symbol,asset_type,as_of_date FROM positions WHERE user_id=$1 AND asset_type<>'cash' AND quantity>0 ORDER BY symbol", Id);
            var main = rows.Count > 0;
            if (!main) rows = sql.Query("SELECT p.symbol,p.asset_type,p.as_of_date FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id WHERE p.user_id=$1 AND a.status='active' AND p.asset_type<>'cash' AND p.quantity>0 ORDER BY p.symbol", Id);
            foreach (var row in rows.OfType<JsonObject>())
            {
                var symbol = row["symbol"]!.GetValue<string>(); var asset = row["asset_type"]!.GetValue<string>(); var position = DateOnly.Parse(row["as_of_date"]!.GetValue<string>());
                var key = asset + ":" + symbol;
                if (!DateOnly.TryParse(priceDates[key]?.GetValue<string>(), out var recent) || recent > today || position > today) Add("data_unknown", symbol, "价格或净值日期未知，不能确认数据有效");
                else if (today.DayNumber - recent.DayNumber > settings.StaleAfterDays || today.DayNumber - position.DayNumber > settings.StaleAfterDays)
                    Add("data_stale", symbol, "价格、净值或持仓快照超过设置时限", new() { ["data_date"] = recent.ToString("yyyy-MM-dd"), ["position_date"] = position.ToString("yyyy-MM-dd") });
                if (asset == "fund")
                {
                    try { fees.Current(sql, Id, symbol, today); }
                    catch (Exception error) when (error is BusinessError or ArgumentException or JsonException or InvalidOperationException)
                    { Add("fee_evidence", symbol, "缺少当前有效的人工核验渠道费率；费用不能视为零"); }
                }
            }
            if (rows.Count > 0)
            {
                var point = sql.Query("SELECT as_of_date,unit_nav,high_water_mark FROM portfolio_valuations WHERE user_id=$1 ORDER BY as_of_date DESC,generated_at DESC LIMIT 1", Id).FirstOrDefault();
                var nav = point?["unit_nav"] is null ? 0 : decimal.Parse(point["unit_nav"]!.GetValue<string>(), CultureInfo.InvariantCulture);
                var high = point?["high_water_mark"] is null ? 0 : decimal.Parse(point["high_water_mark"]!.GetValue<string>(), CultureInfo.InvariantCulture);
                var date = point is null ? DateOnly.MinValue : DateOnly.Parse(point["as_of_date"]!.GetValue<string>());
                if (!main || point is null || nav <= 0 || high < nav || date > today || today.DayNumber - date.DayNumber > settings.StaleAfterDays || rows.OfType<JsonObject>().Any(r => DateOnly.Parse(r["as_of_date"]!.GetValue<string>()) > date))
                    Add("drawdown_unknown", "portfolio", "有效账户净值或高水位不足，当前回撤未知");
                else if (1 - nav / high >= (decimal)settings.DrawdownThreshold)
                    Add("drawdown", "portfolio", "账户净值回撤达到设置阈值", new() { ["drawdown"] = (double)(1 - nav / high), ["threshold"] = settings.DrawdownThreshold, ["as_of_date"] = date.ToString("yyyy-MM-dd") });
            }
            var failures = sql.Query("SELECT report_id AS id,error_code,updated_at AS occurred_at FROM market_advisor_reports WHERE user_id=$1 AND status='failed' AND updated_at>=now()-make_interval(days=>$2) UNION ALL SELECT job_id,error_code,created_at FROM agent_research_jobs WHERE user_id=$1 AND status IN ('failed','timed_out','interrupted') AND created_at>=now()-make_interval(days=>$2) ORDER BY occurred_at DESC LIMIT 200", Id, settings.TaskFailureDays);
            foreach (var row in failures.OfType<JsonObject>()) Add("task_failure", row["id"]!.GetValue<string>(), "研究任务失败，请检查任务详情", new() { ["error_code"] = row["error_code"]?.DeepClone(), ["occurred_at"] = row["occurred_at"]?.DeepClone() });
            sql.Execute("UPDATE user_alert_center SET active=false WHERE user_id=$1", Id);
            foreach (var alert in found)
                sql.Execute("INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged) VALUES($1,$2,$3,$4,$5::json,now(),now(),true,false) ON CONFLICT(user_id,kind,subject) DO UPDATE SET detail=EXCLUDED.detail,last_seen=now(),active=true", Id, Guid.NewGuid().ToString("N"), alert.Kind, alert.Subject, alert.Detail.ToJsonString());
            return new JsonObject { ["items"] = sql.Query("SELECT id,kind,subject,detail,first_seen,last_seen,acknowledged FROM user_alert_center WHERE user_id=$1 AND active ORDER BY acknowledged,first_seen DESC", Id), ["settings"] = JsonSerializer.SerializeToNode(settings, Options), ["scope"] = "current_account", ["unknown_is_safe"] = false };
        }));
    }
}
