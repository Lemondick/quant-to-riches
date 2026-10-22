using System.Text.Json.Nodes;
using System.Text.Json;
using System.Globalization;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch2 任务3/4 后端配合：赎回费率提醒定时任务 + 基金体检 Scheduler job。
/// 赎回提醒：每日按用户持仓批次调 worker op fund.redemption_windows（契约§2），
///   next_tier_at 距今 ≤ QUANT_REDEMPTION_REMIND_DAYS（默认14天）时写 user_alert_center 提醒（kind=redemption_window）。
/// 基金体检：按 QUANT_HEALTH_CHECK_CRON（默认"0 6 * * 1"周一06:00，北京时间）调 fund.health_check，
///   结果 JSON 存 site_settings（key=fund_health_check_latest:{userId}，避免新迁移）；critical 发现同步写用户告警。
/// 口径假设（commit 注明）：批次取每只基金最新快照（fund_lot_snapshots 最新 id）的剩余份额；cron 解析仅支持数字与 *。
/// </summary>
public sealed class FundMaintenanceService(AccountDatabase database, ComputeService compute, QuantOptions options, FundFeeRepository fees)
{
    private DateOnly _lastRedemptionCheck = DateOnly.MinValue;
    private DateOnly _lastHealthRun = DateOnly.MinValue;

    public void CheckRedemptionReminders()
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        if (_lastRedemptionCheck == today) return;
        _lastRedemptionCheck = today;
        var remindDays = options.RedemptionRemindDays;
        try
        {
            foreach (var userId in ActiveUsers())
            {
                try { CheckUserRedemption(userId, remindDays); }
                catch (Exception) { /* 单个用户失败不中断其余 */ }
            }
        }
        catch (Exception) { /* 失败不中断 Tick，次日重试 */ }
    }

    private void CheckUserRedemption(string userId, int remindDays)
    {
        var lots = database.Read(sql =>
        {
            var rows = sql.Query("""
            SELECT s.symbol AS fund_code, l.lot_id, l.confirmed_on::text AS acquired_at, l.remaining_units::text AS amount
            FROM fund_lot_snapshots s JOIN fund_holding_lots l ON l.user_id=s.user_id AND l.snapshot_id=s.id
            WHERE s.user_id=$1 AND l.remaining_units > 0
              AND s.id = (SELECT max(id) FROM fund_lot_snapshots WHERE user_id=s.user_id AND symbol=s.symbol)
            ORDER BY s.symbol, l.confirmed_on, l.lot_id
            """, userId).OfType<JsonObject>();
            var verified = new List<JsonObject>();
            foreach (var row in rows)
            {
                var symbol = row["fund_code"]!.GetValue<string>();
                try
                {
                    var schedule = fees.Current(sql, userId, symbol, FundFeeRepository.Today());
                    var nav = sql.Scalar("""
                    SELECT sum(market_value)/NULLIF(sum(quantity),0) FROM (
                      SELECT quantity,market_value FROM positions WHERE user_id=$1 AND symbol=$2 AND quantity>0
                      UNION ALL SELECT p.quantity,p.market_value FROM account_positions p
                      JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id
                      WHERE p.user_id=$1 AND p.symbol=$2 AND p.quantity>0 AND a.status='active'
                    ) held
                    """, userId, symbol);
                    if (nav is not decimal currentNav || currentNav <= 0) continue;
                    row["amount"] = (decimal.Parse(row["amount"]!.GetValue<string>(), CultureInfo.InvariantCulture) * currentNav).ToString(CultureInfo.InvariantCulture);
                    row["redemption_tiers"] = JsonSerializer.SerializeToNode(schedule.RedemptionTiers, FinanceJson.Options);
                    verified.Add(row);
                }
                catch (Exception error) when (error is UnverifiedFeeScheduleError or ArgumentException) { /* 无已验证费率证据，跳过。 */ }
            }
            return verified;
        });
        if (lots.Count == 0) return;
        var payload = new JsonObject
        {
            ["lots"] = new JsonArray(lots.Select(l => (JsonNode)new JsonObject
            {
                ["fund_code"] = l["fund_code"]?.GetValue<string>(),
                ["lot_id"] = l["lot_id"]?.GetValue<string>(),
                ["acquired_at"] = l["acquired_at"]?.GetValue<string>(),
                ["amount"] = l["amount"]?.GetValue<string>(),
                ["redemption_tiers"] = l["redemption_tiers"]?.DeepClone(),
            }).ToArray()),
            ["days_ahead"] = remindDays,
        };
        var result = compute.Run(userId, "fund.redemption_windows", payload, CancellationToken.None, seconds: 180)
            .GetAwaiter().GetResult()?.AsObject();
        var windows = result?["windows"]?.AsArray()?.OfType<JsonObject>().ToList() ?? [];
        database.WriteNonLedger(userId, sql =>
        {
            var activeKeys = new List<string>();
            foreach (var window in windows)
            {
                var daysLeft = window["days_left"]?.GetValue<double?>();
                if (daysLeft is null || daysLeft > remindDays) continue;
                var fundCode = window["fund_code"]?.GetValue<string>() ?? "";
                var lotId = window["lot_id"]?.GetValue<string>() ?? "";
                var subject = fundCode + ":" + lotId;
                activeKeys.Add(subject);
                var detail = new JsonObject
                {
                    ["severity"] = "warning",
                    ["message"] = $"{fundCode} 批次 {lotId} 将在 {daysLeft:0.#} 天后进入更低赎回费率档（{TierRate(window["current_tier"])} → {TierRate(window["next_tier"])}），" +
                        $"预计可省约 {window["save_estimate"]?.GetValue<string>()} 元。如不急用可等降档后再赎回。",
                    ["fund_code"] = fundCode,
                    ["lot_id"] = lotId,
                    ["current_tier"] = window["current_tier"]?.DeepClone(),
                    ["next_tier"] = window["next_tier"]?.DeepClone(),
                    ["next_tier_at"] = window["next_tier_at"]?.DeepClone(),
                    ["days_left"] = daysLeft,
                    ["save_estimate"] = window["save_estimate"]?.DeepClone(),
                };
                sql.Execute("""
                    INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged)
                    VALUES($1,$2,'redemption_window',$3,$4::json,now(),now(),true,false)
                    ON CONFLICT(user_id,kind,subject) DO UPDATE SET detail=EXCLUDED.detail,last_seen=now(),active=true,acknowledged=false
                    """, userId, Guid.NewGuid().ToString("N"), subject, detail.ToJsonString());
            }
            // 已过提醒窗口的批次视为恢复 → 关闭告警。
            sql.Execute("UPDATE user_alert_center SET active=false WHERE user_id=$1 AND kind='redemption_window' AND active AND NOT (subject = ANY($2))",
                userId, activeKeys.ToArray());
            return 0;
        });
    }

    private static string TierRate(JsonNode? tier) =>
        decimal.TryParse(tier?["rate"]?.ToString(), NumberStyles.Number, CultureInfo.InvariantCulture, out var rate)
            ? (rate * 100).ToString("0.####", CultureInfo.InvariantCulture) + "%" : "未知";

    public void CheckFundHealth()
    {
        var now = DateTime.UtcNow.AddHours(8);
        var today = DateOnly.FromDateTime(now);
        if (_lastHealthRun == today || !CronMatches(options.HealthCheckCron, now)) return;
        _lastHealthRun = today;
        try
        {
            foreach (var userId in ActiveUsers())
            {
                try { CheckUserHealth(userId); }
                catch (Exception) { /* 单个用户失败不中断其余 */ }
            }
        }
        catch (Exception) { /* 失败不中断 Tick，下次 cron 命中重试 */ }
    }

    /// <summary>手动触发单个用户的体检（POST api/v1/funds/health/run），绕过 cron 门控。</summary>
    public void CheckFundHealthForced(string userId)
    {
        try { CheckUserHealth(userId); }
        catch (Exception) { throw new QuantAssistant.Core.Errors.ApplicationFailure("worker_unavailable", 503, "体检计算未完成，请稍后重试。"); }
    }

    private void CheckUserHealth(string userId)
    {
        var funds = database.Read(sql => sql.Query("""
            SELECT DISTINCT p.symbol AS code, COALESCE(i.name, p.symbol) AS name
            FROM (SELECT user_id, symbol FROM positions WHERE user_id=$1 AND asset_type='fund' AND quantity>0
                  UNION
                  SELECT p.user_id, p.symbol FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id
                  WHERE p.user_id=$1 AND a.status='active' AND p.asset_type='fund' AND p.quantity>0) p
            LEFT JOIN instruments i ON i.user_id=p.user_id AND i.symbol=p.symbol
            """, userId)).OfType<JsonObject>().ToList();
        if (funds.Count == 0) return;
        var payload = new JsonObject
        {
            ["funds"] = new JsonArray(funds.Select(f => (JsonNode)new JsonObject
            {
                ["code"] = f["code"]?.GetValue<string>(),
                ["name"] = f["name"]?.GetValue<string>(),
            }).ToArray()),
        };
        var result = (compute.Run(userId, "fund.health_check", payload, CancellationToken.None, seconds: 300)
            .GetAwaiter().GetResult()?.AsObject()) ?? new JsonObject { ["reports"] = new JsonArray() };
        result["checked_at"] = DateTimeOffset.UtcNow.ToString("O");
        database.WriteNonLedger(userId, sql =>
        {
            // 结果存 site_settings（复用现有表，避免新迁移）。
            sql.Execute("INSERT INTO site_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                "fund_health_check_latest:" + userId, result.ToJsonString());
            // critical 发现同步写用户告警。
            var activeKeys = new List<string>();
            foreach (var report in result["reports"]?.AsArray().OfType<JsonObject>() ?? [])
            {
                var code = report["code"]?.GetValue<string>() ?? "";
                foreach (var finding in report["findings"]?.AsArray().OfType<JsonObject>() ?? [])
                {
                    if (finding["severity"]?.GetValue<string>() != "critical") continue;
                    var subject = code + ":" + (finding["kind"]?.GetValue<string>() ?? "finding");
                    activeKeys.Add(subject);
                    var detail = new JsonObject
                    {
                        ["severity"] = "critical",
                        ["message"] = $"{code} 体检发现严重问题（{finding["kind"]?.GetValue<string>()}）：{finding["detail"]?.GetValue<string>()}",
                        ["fund_code"] = code,
                        ["kind"] = finding["kind"]?.DeepClone(),
                        ["detail"] = finding["detail"]?.DeepClone(),
                        ["evidence"] = finding["evidence"]?.DeepClone(),
                    };
                    sql.Execute("""
                        INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged)
                        VALUES($1,$2,'fund_health',$3,$4::json,now(),now(),true,false)
                        ON CONFLICT(user_id,kind,subject) DO UPDATE SET detail=EXCLUDED.detail,last_seen=now(),active=true,acknowledged=false
                        """, userId, Guid.NewGuid().ToString("N"), subject, detail.ToJsonString());
                }
            }
            sql.Execute("UPDATE user_alert_center SET active=false WHERE user_id=$1 AND kind='fund_health' AND active AND NOT (subject = ANY($2))",
                userId, activeKeys.ToArray());
            return 0;
        });
    }

    private List<string> ActiveUsers() =>
        database.Read(sql => sql.Query("SELECT id FROM users WHERE status='active' ORDER BY id"))
            .OfType<JsonObject>().Select(r => r["id"]!.GetValue<string>()).ToList();

    /// <summary>最小 cron 解析：5 段（分 时 日 月 周），仅支持数字与 *，时间为北京时间。</summary>
    internal static bool CronMatches(string cron, DateTime now)
    {
        var parts = cron.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length != 5) return false;
        bool Match(string part, int value) => part == "*" || (int.TryParse(part, out var n) && n == value);
        return Match(parts[0], now.Minute) && Match(parts[1], now.Hour) && Match(parts[2], now.Day)
            && Match(parts[3], now.Month) && Match(parts[4], (int)now.DayOfWeek);
    }
}
