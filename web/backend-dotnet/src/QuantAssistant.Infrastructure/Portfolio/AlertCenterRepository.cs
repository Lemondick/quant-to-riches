using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>告警中心设置。</summary>
public sealed record AlertSettings(long Revision = 0, int StaleAfterDays = 7, double DrawdownThreshold = .1, int TaskFailureDays = 7);

/// <summary>E5：告警中心仓储（原 AlertsController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class AlertCenterRepository(IAccountDatabase database, FundFeeRepository fees)
{
    private static readonly JsonSerializerOptions Options = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    private static AlertSettings Settings(IAccountSql sql, string user) => sql.Scalar("SELECT payload::text FROM alert_center_settings WHERE user_id=$1 AND id=1", user) is string raw ? JsonSerializer.Deserialize<AlertSettings>(raw, Options)! : new();

    public async Task<AlertSettings> GetSettingsAsync(string userId, CancellationToken cancellation = default) =>
        await database.ReadAsync(sql => Task.FromResult(Settings(sql, userId)), cancellation);

    public async Task<AlertSettings> SaveSettingsAsync(string userId, AlertSettings body, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var current = Settings(sql, userId);
            if (current.Revision != body.Revision) throw new ApplicationFailure("alert_settings_conflict", 409, "告警阈值已变化，请重新读取。");
            body = body with { Revision = current.Revision + 1 };
            await sql.ExecuteAsync("INSERT INTO alert_center_settings(user_id,id,payload) VALUES($1,1,$2::json) ON CONFLICT(user_id,id) DO UPDATE SET payload=EXCLUDED.payload", userId, JsonSerializer.Serialize(body, Options));
            return body;
        }, cancellation);

    public async Task<int> AcknowledgeAsync(string userId, string alertId, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId,
            sql => sql.ExecuteAsync("UPDATE user_alert_center SET acknowledged=true WHERE user_id=$1 AND id=$2", userId, alertId), cancellation);

    public async Task<JsonObject> EvaluateAsync(string userId, JsonObject priceDates, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var settings = Settings(sql, userId); var today = FundFeeRepository.Today(); var found = new List<(string Kind, string Subject, JsonObject Detail)>();
            void Add(string kind, string subject, string message, JsonObject? detail = null)
            { detail ??= new(); detail["message"] = message; found.Add((kind, subject, detail)); }
            var rows = await sql.QueryAsync("SELECT symbol,asset_type,as_of_date FROM positions WHERE user_id=$1 AND asset_type<>'cash' AND quantity>0 ORDER BY symbol", userId);
            var main = rows.Count > 0;
            if (!main) rows = await sql.QueryAsync("SELECT p.symbol,p.asset_type,p.as_of_date FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id WHERE p.user_id=$1 AND a.status='active' AND p.asset_type<>'cash' AND p.quantity>0 ORDER BY p.symbol", userId);
            foreach (var row in rows.OfType<JsonObject>())
            {
                var symbol = row["symbol"]!.GetValue<string>(); var asset = row["asset_type"]!.GetValue<string>(); var position = DateOnly.Parse(row["as_of_date"]!.GetValue<string>());
                var key = asset + ":" + symbol;
                if (!DateOnly.TryParse(priceDates[key]?.GetValue<string>(), out var recent) || recent > today || position > today) Add("data_unknown", symbol, "价格或净值日期未知，不能确认数据有效");
                else if (today.DayNumber - recent.DayNumber > settings.StaleAfterDays || today.DayNumber - position.DayNumber > settings.StaleAfterDays)
                    Add("data_stale", symbol, "价格、净值或持仓快照超过设置时限", new() { ["data_date"] = recent.ToString("yyyy-MM-dd"), ["position_date"] = position.ToString("yyyy-MM-dd") });
                if (asset == "fund")
                {
                    try { fees.Current(sql, userId, symbol, today); }
                    catch (Exception error) when (error is BusinessError or ArgumentException or JsonException or InvalidOperationException)
                    { Add("fee_evidence", symbol, "缺少当前有效的人工核验渠道费率；费用不能视为零"); }
                }
            }
            if (rows.Count > 0)
            {
                var point = (await sql.QueryAsync("SELECT as_of_date,unit_nav,high_water_mark FROM portfolio_valuations WHERE user_id=$1 ORDER BY as_of_date DESC,generated_at DESC LIMIT 1", userId)).FirstOrDefault();
                var nav = point?["unit_nav"] is null ? 0 : decimal.Parse(point["unit_nav"]!.GetValue<string>(), CultureInfo.InvariantCulture);
                var high = point?["high_water_mark"] is null ? 0 : decimal.Parse(point["high_water_mark"]!.GetValue<string>(), CultureInfo.InvariantCulture);
                var date = point is null ? DateOnly.MinValue : DateOnly.Parse(point["as_of_date"]!.GetValue<string>());
                if (!main || point is null || nav <= 0 || high < nav || date > today || today.DayNumber - date.DayNumber > settings.StaleAfterDays || rows.OfType<JsonObject>().Any(r => DateOnly.Parse(r["as_of_date"]!.GetValue<string>()) > date))
                    Add("drawdown_unknown", "portfolio", "有效账户净值或高水位不足，当前回撤未知");
                else if (1 - nav / high >= (decimal)settings.DrawdownThreshold)
                    Add("drawdown", "portfolio", "账户净值回撤达到设置阈值", new() { ["drawdown"] = (double)(1 - nav / high), ["threshold"] = settings.DrawdownThreshold, ["as_of_date"] = date.ToString("yyyy-MM-dd") });
            }
            var failures = await sql.QueryAsync("SELECT report_id AS id,error_code,updated_at AS occurred_at FROM market_advisor_reports WHERE user_id=$1 AND status='failed' AND updated_at>=now()-make_interval(days=>$2) UNION ALL SELECT job_id,error_code,created_at FROM agent_research_jobs WHERE user_id=$1 AND status IN ('failed','timed_out','interrupted') AND created_at>=now()-make_interval(days=>$2) ORDER BY occurred_at DESC LIMIT 200", userId, settings.TaskFailureDays);
            foreach (var row in failures.OfType<JsonObject>()) Add("task_failure", row["id"]!.GetValue<string>(), "研究任务失败，请检查任务详情", new() { ["error_code"] = row["error_code"]?.DeepClone(), ["occurred_at"] = row["occurred_at"]?.DeepClone() });
            await sql.ExecuteAsync("UPDATE user_alert_center SET active=false WHERE user_id=$1", userId);
            foreach (var alert in found)
                await sql.ExecuteAsync("INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged) VALUES($1,$2,$3,$4,$5::json,now(),now(),true,false) ON CONFLICT(user_id,kind,subject) DO UPDATE SET detail=EXCLUDED.detail,last_seen=now(),active=true", userId, Guid.NewGuid().ToString("N"), alert.Kind, alert.Subject, alert.Detail.ToJsonString());
            return new JsonObject { ["items"] = await sql.QueryAsync("SELECT id,kind,subject,detail,first_seen,last_seen,acknowledged FROM user_alert_center WHERE user_id=$1 AND active ORDER BY acknowledged,first_seen DESC", userId), ["settings"] = JsonSerializer.SerializeToNode(settings, Options), ["scope"] = "current_account", ["unknown_is_safe"] = false };
        }, cancellation);
}
