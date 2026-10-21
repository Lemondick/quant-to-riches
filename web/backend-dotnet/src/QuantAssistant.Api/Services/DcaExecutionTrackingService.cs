using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch5 任务4：定投执行跟踪（计划 vs 执行：按期/漏投/金额偏离）。
/// 漏投提醒：某期应投日超过 QUANT_DCA_MISSED_ALERT_DAYS（默认 3 天）仍未登记 done/skipped →
/// 写入 user_alert_center（kind=dca_missed，subject=plan_id:due_date）。由 Scheduler 每日 Tick 触发。
/// 只记录与提醒，不自动扣款、不执行真实交易。
/// </summary>
public sealed class DcaExecutionTrackingService(DcaExecutionRepository repo, AccountDatabase database, QuantOptions options)
{
    private DateOnly _lastMissedCheck = DateOnly.MinValue;

    /// <summary>应投期推算：以 plan.created_at 为锚点、按 frequency 递增，截止到 today（含今日）。</summary>
    internal static List<DateOnly> DueDates(string frequency, DateOnly anchor, DateOnly today)
    {
        var dates = new List<DateOnly>();
        var current = anchor;
        while (current <= today)
        {
            dates.Add(current);
            current = frequency == "weekly" ? current.AddDays(7) : current.AddMonths(1);
        }
        return dates;
    }

    public async Task<JsonObject> TrackingAsync(string userId, CancellationToken cancellation = default)
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        var plans = await repo.ActivePlansAsync(userId, cancellation);
        var executions = await repo.ExecutionsAsync(userId, null, cancellation);
        var byPlan = executions.OfType<JsonObject>()
            .GroupBy(e => e["plan_id"]!.GetValue<string>())
            .ToDictionary(g => g.Key, g => g.ToDictionary(e => e["due_date"]!.GetValue<string>(), e => e, StringComparer.Ordinal),
                StringComparer.Ordinal);
        var items = new JsonArray();
        foreach (var plan in plans.OfType<JsonObject>())
        {
            var planId = plan["id"]!.GetValue<string>();
            var anchor = DateOnly.ParseExact(plan["created_at"]!.GetValue<string>(), "yyyy-MM-dd", CultureInfo.InvariantCulture);
            var dueDates = DueDates(plan["frequency"]!.GetValue<string>(), anchor, today);
            var execByDue = byPlan.TryGetValue(planId, out var dict) ? dict : new Dictionary<string, JsonObject>(StringComparer.Ordinal);
            var baseAmount = decimal.Parse(plan["base_amount"]!.GetValue<string>(), CultureInfo.InvariantCulture);
            int onTime = 0, missed = 0, skipped = 0, pending = 0;
            decimal deviationSum = 0;
            int deviationCount = 0;
            var periods = new JsonArray();
            foreach (var due in dueDates)
            {
                var key = due.ToString("yyyy-MM-dd");
                var state = "pending";
                decimal? amountDeviation = null;
                if (execByDue.TryGetValue(key, out var exec))
                {
                    var status = exec["status"]!.GetValue<string>();
                    if (status == "done")
                    {
                        state = "on_time";
                        onTime++;
                        if (decimal.TryParse(exec["actual_amount"]?.GetValue<string>(), NumberStyles.Float, CultureInfo.InvariantCulture, out var actual) && baseAmount > 0)
                        {
                            amountDeviation = Math.Round(Math.Abs(actual - baseAmount) / baseAmount, 4);
                            deviationSum += amountDeviation.Value;
                            deviationCount++;
                        }
                    }
                    else if (status == "skipped") { state = "skipped"; skipped++; }
                }
                else if ((today.DayNumber - due.DayNumber) > options.DcaMissedAlertDays) { state = "missed"; missed++; }
                else pending++;
                periods.Add(new JsonObject
                {
                    ["due_date"] = key,
                    ["state"] = state,
                    ["planned_amount"] = baseAmount.ToString(CultureInfo.InvariantCulture),
                    ["actual_amount"] = execByDue.TryGetValue(key, out var e2) ? e2["actual_amount"]?.GetValue<string>() : null,
                    ["amount_deviation"] = amountDeviation,
                });
            }
            items.Add(new JsonObject
            {
                ["plan_id"] = planId,
                ["symbol"] = plan["symbol"]!.GetValue<string>(),
                ["frequency"] = plan["frequency"]!.GetValue<string>(),
                ["base_amount"] = plan["base_amount"]!.GetValue<string>(),
                ["expected_periods"] = dueDates.Count,
                ["on_time"] = onTime,
                ["missed"] = missed,
                ["skipped"] = skipped,
                ["pending"] = pending,
                ["avg_amount_deviation"] = deviationCount > 0 ? Math.Round((double)(deviationSum / deviationCount), 4) : (double?)null,
                ["periods"] = periods,
            });
        }
        return new JsonObject
        {
            ["missed_alert_days"] = options.DcaMissedAlertDays,
            ["plans"] = items,
        };
    }

    /// <summary>Scheduler 每日执行（Tick 10 秒一次，日期门控保证每天只跑一次）：漏投告警。</summary>
    public void CheckMissedAlerts()
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        if (_lastMissedCheck == today) return;
        _lastMissedCheck = today;
        try
        {
            var users = database.Read(sql => sql.Query("SELECT id FROM users WHERE status='active' ORDER BY id"));
            foreach (var user in users.OfType<JsonObject>())
            {
                var userId = user["id"]!.GetValue<string>();
                try { CheckUserMissed(userId, today).GetAwaiter().GetResult(); }
                catch (Exception) { /* 单个用户失败不中断其余 */ }
            }
        }
        catch (Exception) { /* 告警 job 失败不中断 Tick，次日重试 */ }
    }

    private async Task CheckUserMissed(string userId, DateOnly today)
    {
        var tracking = await TrackingAsync(userId);
        foreach (var plan in tracking["plans"]!.AsArray().OfType<JsonObject>())
        {
            var planId = plan["plan_id"]!.GetValue<string>();
            foreach (var period in plan["periods"]!.AsArray().OfType<JsonObject>())
            {
                if (period["state"]!.GetValue<string>() != "missed") continue;
                var due = period["due_date"]!.GetValue<string>();
                var overdueDays = today.DayNumber - DateOnly.ParseExact(due, "yyyy-MM-dd", CultureInfo.InvariantCulture).DayNumber;
                RaiseMissedAlert(userId, planId, plan["symbol"]!.GetValue<string>(), due, overdueDays);
            }
        }
    }

    private void RaiseMissedAlert(string userId, string planId, string symbol, string dueDate, int overdueDays) =>
        database.WriteNonLedger(userId, sql =>
        {
            var detail = new JsonObject
            {
                ["severity"] = "warning",
                ["message"] = $"智能定投漏投：{symbol} {dueDate} 应投已逾期 {overdueDays} 天仍未登记，请手动补投或标记跳过（系统不会自动扣款）。",
                ["kind"] = "missed",
                ["plan_id"] = planId,
                ["due_date"] = dueDate,
                ["overdue_days"] = overdueDays,
            };
            sql.Execute("""
                INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged)
                VALUES($1,$2,'dca_missed',$3,$4::json,now(),now(),true,false)
                ON CONFLICT(user_id,kind,subject) DO UPDATE SET detail=EXCLUDED.detail,last_seen=now(),active=true,acknowledged=false
                """, userId, Guid.NewGuid().ToString("N"), planId + ":" + dueDate, detail.ToJsonString());
            return 0;
        });
}
