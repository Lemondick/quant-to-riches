using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch2 任务5：智能定投回测透调 + 执行提醒（只提醒，不自动扣款）。
/// 提醒分两种：到期提醒（next_run_date 到期）与估值触发提醒（pe 分位 vs 规则阈值）。
/// 估值分档假设（commit 注明）：pe_pct>=halt_pct→暂停；pe_pct>=70→高估削减；pe_pct<=30→低估加投；其余按基准。
/// 提醒写入 user_alert_center（kind=smart_dca_reminder），到期提醒发出后按 frequency 推进 next_run_date。
/// </summary>
public sealed class SmartDcaService(SmartDcaRepository plans, ComputeService compute, AccountDatabase database)
{
    public async Task<JsonNode?> BacktestAsync(string userId, string planId, DateOnly start, DateOnly end, CancellationToken cancellation)
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        if (start >= end || start < new DateOnly(1990, 1, 1) || end > today)
            throw new ArgumentException("回测起止日期无效（须 start<end 且不晚于今日）。");
        var plan = await plans.GetAsync(userId, planId, cancellation);
        var payload = new JsonObject
        {
            ["index_symbol"] = plan["symbol"]!.GetValue<string>(),
            ["start"] = start.ToString("yyyy-MM-dd"), ["end"] = end.ToString("yyyy-MM-dd"),
            ["base_amount"] = decimal.Parse(plan["base_amount"]!.GetValue<string>(), CultureInfo.InvariantCulture),
            ["rules"] = plan["rules"]!.DeepClone(),
        };
        var result = await compute.Run(userId, "dca.smart_plan_backtest", payload, cancellation, seconds: 300);
        return result ?? throw new ApplicationFailure("worker_unavailable", 503, "回测计算未完成，请稍后重试。");
    }

    private DateOnly _lastReminderCheck = DateOnly.MinValue;

    /// <summary>Scheduler 每日执行（Tick 10 秒一次，日期门控保证每天只跑一次）。</summary>
    public void CheckReminders()
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        if (_lastReminderCheck == today) return;
        _lastReminderCheck = today;
        try
        {
            var due = plans.DuePlansAsync(today).GetAwaiter().GetResult();
            foreach (var row in due.OfType<JsonObject>())
            {
                try
                {
                    var userId = row["user_id"]!.GetValue<string>();
                    var planId = row["id"]!.GetValue<string>();
                    RaiseReminder(userId, planId, "due",
                        $"智能定投到期：{row["symbol"]!.GetValue<string>()} 今日应投，请手动执行（系统不会自动扣款）。",
                        new JsonObject { ["base_amount"] = row["base_amount"]?.GetValue<string>(), ["frequency"] = row["frequency"]?.GetValue<string>() });
                    var next = row["frequency"]!.GetValue<string>() == "weekly" ? today.AddDays(7) : today.AddMonths(1);
                    plans.AdvanceNextRunAsync(userId, planId, next).GetAwaiter().GetResult();
                }
                catch (Exception) { /* 单个计划失败不中断其余 */ }
            }
            CheckValuationTriggers(today);
        }
        catch (Exception) { /* 提醒 job 失败不中断 Tick，次日重试 */ }
    }

    private void CheckValuationTriggers(DateOnly today)
    {
        var active = plans.ActivePlansAsync().GetAwaiter().GetResult().OfType<JsonObject>().ToList();
        if (active.Count == 0) return;
        var symbols = active.Select(p => p["symbol"]!.GetValue<string>()).Distinct(StringComparer.Ordinal).ToArray();
        var payload = new JsonObject
        {
            ["indices"] = new JsonArray(symbols.Select(s => (JsonNode)new JsonObject { ["symbol"] = s, ["market"] = "CN" }).ToArray()),
            ["years"] = 10,
        };
        var result = compute.Run("system:smart-dca", "valuation.percentiles", payload, CancellationToken.None, seconds: 180)
            .GetAwaiter().GetResult()?.AsObject();
        var percentiles = result?["items"]?.AsArray() ?? new JsonArray();
        var bySymbol = percentiles.OfType<JsonObject>()
            .Where(i => i["symbol"]?.GetValue<string>() is { } s && symbols.Contains(s, StringComparer.Ordinal))
            .ToDictionary(i => i["symbol"]!.GetValue<string>(), i => i, StringComparer.Ordinal);
        foreach (var plan in active)
        {
            try
            {
                var userId = plan["user_id"]!.GetValue<string>();
                var planId = plan["id"]!.GetValue<string>();
                var symbol = plan["symbol"]!.GetValue<string>();
                if (!bySymbol.TryGetValue(symbol, out var item)) continue;
                var pePct = item["pe_pct"]?.GetValue<double?>();
                if (pePct is null) continue;
                var rules = plan["rules"]!.AsObject();
                var halt = rules["halt_pct"]!.GetValue<double>();
                var cut = rules["high_pct_cut"]!.GetValue<double>();
                var boost = rules["low_pct_boost"]!.GetValue<double>();
                var baseAmount = decimal.Parse(plan["base_amount"]!.GetValue<string>(), CultureInfo.InvariantCulture);
                decimal suggest;
                string label;
                if (pePct >= halt) { suggest = 0m; label = "暂停"; }
                else if (pePct >= 70) { suggest = Math.Round(baseAmount * (decimal)(1 - cut), 2); label = "高估削减"; }
                else if (pePct <= 30) { suggest = Math.Round(baseAmount * (decimal)(1 + boost), 2); label = "低估加投"; }
                else continue; // 估值中性：不打扰。
                RaiseReminder(userId, planId, "valuation",
                    $"智能定投估值触发（{label}）：{symbol} PE 分位 {pePct:0.#}%，建议本期投入 {suggest} 元（基准 {baseAmount} 元），请手动执行（系统不会自动扣款）。",
                    new JsonObject { ["pe_pct"] = pePct, ["suggested_amount"] = suggest, ["trigger"] = label });
            }
            catch (Exception) { /* 单个计划失败不中断其余 */ }
        }
    }

    private void RaiseReminder(string userId, string planId, string kind, string message, JsonObject extra) =>
        database.WriteNonLedger(userId, sql =>
        {
            var detail = new JsonObject { ["severity"] = "warning", ["message"] = message, ["kind"] = kind };
            foreach (var pair in extra) detail[pair.Key] = pair.Value?.DeepClone();
            sql.Execute("""
                INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged)
                VALUES($1,$2,'smart_dca_reminder',$3,$4::json,now(),now(),true,false)
                ON CONFLICT(user_id,kind,subject) DO UPDATE SET detail=EXCLUDED.detail,last_seen=now(),active=true,acknowledged=false
                """, userId, Guid.NewGuid().ToString("N"), planId + ":" + kind, detail.ToJsonString());
            return 0;
        });
}
