using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch5 任务1：目标规划投影（api/v1/goals/{id}/project）。
/// current_amount 取账本净资产（ListPositionsAsync 市值合计口径），透调 worker op goal.project。
/// 若 worker op 尚未实现（流 B 未完成），本地 stub 顶上并在响应中注明 calculation_source，
/// 联调时直接替换即可（契约见 qr5-contracts.md；payload/返回形状与 worker 约定一致）。
/// 进度偏离口径：线性假设（预期完成度=已流逝天数/总天数），实际完成度=current_amount/target_amount，
/// 绝对差超 QUANT_GOAL_DEVIATION_THRESHOLD（默认 10%）即写入 user_alert_center（kind=goal_deviation）。
/// 只测算、不执行真实交易。
/// </summary>
public sealed class GoalProjectionService(GoalTargetRepository goals, ComputeService compute, AccountDatabase database,
    ILedgerRepository ledger, QuantOptions options)
{
    private const string Disclaimer = "假设测算非承诺：未来收益不确定，测算结果不构成任何保证。";

    /// <summary>账本净资产：持仓市值合计（与 /api/v1/portfolio 同口径）。系统暂无负债登记，负债=0。</summary>
    public async Task<decimal> LedgerNetWorthAsync(string userId, CancellationToken cancellation = default) =>
        (await ledger.ListPositionsAsync(userId, cancellation)).Sum(p => p.MarketValue);

    public async Task<JsonObject> ProjectAsync(string userId, string goalId, decimal? monthlyAmount,
        CancellationToken cancellation = default)
    {
        var goal = await goals.GetAsync(userId, goalId, cancellation);
        var netWorth = await LedgerNetWorthAsync(userId, cancellation);
        await goals.RefreshCurrentAmountAsync(userId, goalId, netWorth, cancellation);
        var current = netWorth;
        var target = decimal.Parse(goal["target_amount"]!.GetValue<string>(), CultureInfo.InvariantCulture);
        var targetDate = DateOnly.ParseExact(goal["target_date"]!.GetValue<string>(), "yyyy-MM-dd", CultureInfo.InvariantCulture);
        var assumed = double.Parse(goal["assumed_annual_return"]!.GetValue<string>(), CultureInfo.InvariantCulture);

        var payload = new JsonObject
        {
            ["target_amount"] = target.ToString(CultureInfo.InvariantCulture),
            ["target_date"] = targetDate.ToString("yyyy-MM-dd"),
            ["current_amount"] = current.ToString(CultureInfo.InvariantCulture),
            ["assumed_annual_return"] = assumed,
            ["frequency"] = "monthly",
        };
        if (monthlyAmount is { } monthly) payload["monthly_amount"] = monthly.ToString(CultureInfo.InvariantCulture);

        JsonObject result;
        string source;
        try
        {
            var worker = await compute.Run(userId, "goal.project", payload, cancellation, seconds: 60);
            if (worker is null) throw new ApplicationFailure("worker_unavailable", 503, "计算进程未返回结果。");
            result = worker.AsObject();
            source = "worker";
        }
        catch (ApplicationFailure error) when (error.Code is "worker_unavailable" or "invalid_request")
        {
            // 流 B 尚未实现 goal.project：本地 stub 顶上（纯计算公式，契约一致）。
            result = LocalProject(current, target, targetDate, monthlyAmount, assumed);
            source = "local_stub";
        }
        result["calculation_source"] = source;
        result["goal_id"] = goalId;
        result["as_of_net_worth"] = current.ToString(CultureInfo.InvariantCulture);

        // 进度偏离告警（可配阈值，默认 10%）。
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        var created = DateOnly.ParseExact(goal["created_at"]!.GetValue<string>()[..10], "yyyy-MM-dd", CultureInfo.InvariantCulture);
        var totalDays = Math.Max(1, targetDate.DayNumber - created.DayNumber);
        var elapsedDays = Math.Clamp(today.DayNumber - created.DayNumber, 0, totalDays);
        var expected = (double)elapsedDays / totalDays;
        var actual = target > 0 ? (double)current / (double)target : 0;
        var deviation = Math.Abs(actual - expected);
        var deviated = deviation > options.GoalDeviationThreshold;
        result["expected_progress"] = Math.Round(expected, 4);
        result["actual_progress"] = Math.Round(actual, 4);
        result["progress_deviation"] = Math.Round(deviation, 4);
        result["deviation_threshold"] = options.GoalDeviationThreshold;
        result["deviation_alert"] = deviated;
        if (deviated)
            RaiseDeviationAlert(userId, goalId, goal["name"]!.GetValue<string>(), expected, actual, deviation);
        else
            ClearDeviationAlert(userId, goalId);
        return result;
    }

    /// <summary>
    /// 本地 stub：与 worker op goal.project 同形状。
    /// 公式：未来值 FV = current*(1+r)^y + monthly * (((1+r/12)^n - 1)/(r/12))；
    /// required_monthly 倒推：每月定投 n 期、月复利 r/12 下达到 target 所需月投额（缺 monthly_amount 时）。
    /// </summary>
    internal static JsonObject LocalProject(decimal current, decimal target, DateOnly targetDate,
        decimal? monthlyAmount, double assumedAnnualReturn)
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        var months = Math.Max(1, (targetDate.Year - today.Year) * 12 + targetDate.Month - today.Month);
        var years = months / 12.0;
        var monthlyRate = assumedAnnualReturn / 12.0;
        var growth = Math.Pow(1 + assumedAnnualReturn, years);
        var annuityFactor = monthlyRate > 0 ? (Math.Pow(1 + monthlyRate, months) - 1) / monthlyRate : months;
        var projected = (double)current * growth + (double)(monthlyAmount ?? 0m) * annuityFactor;
        var requiredMonthly = monthlyAmount is null
            ? Math.Max(0, ((double)target - (double)current * growth) / annuityFactor)
            : (double)monthlyAmount.Value;
        var gap = (double)target - projected;
        return new JsonObject
        {
            ["required_monthly"] = Math.Round(requiredMonthly, 2),
            ["projected_amount"] = Math.Round(projected, 2),
            ["gap"] = Math.Round(gap, 2),
            ["on_track"] = gap <= 0,
            ["assumptions"] = new JsonObject
            {
                ["frequency"] = "monthly",
                ["months_remaining"] = months,
                ["assumed_annual_return"] = assumedAnnualReturn,
                ["compounding"] = "monthly",
            },
            ["disclaimer"] = Disclaimer,
        };
    }

    private void RaiseDeviationAlert(string userId, string goalId, string name, double expected, double actual, double deviation) =>
        database.WriteNonLedger(userId, sql =>
        {
            var detail = new JsonObject
            {
                ["severity"] = "warning",
                ["message"] = $"目标「{name}」进度偏离：实际完成 {actual:P1}、预期 {expected:P1}，偏离 {deviation:P1}（阈值 {options.GoalDeviationThreshold:P0}）。",
                ["kind"] = "deviation",
                ["goal_id"] = goalId,
                ["expected_progress"] = Math.Round(expected, 4),
                ["actual_progress"] = Math.Round(actual, 4),
                ["progress_deviation"] = Math.Round(deviation, 4),
            };
            sql.Execute("""
                INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged)
                VALUES($1,$2,'goal_deviation',$3,$4::json,now(),now(),true,false)
                ON CONFLICT(user_id,kind,subject) DO UPDATE SET detail=EXCLUDED.detail,last_seen=now(),active=true,acknowledged=false
                """, userId, Guid.NewGuid().ToString("N"), goalId, detail.ToJsonString());
            return 0;
        });

    private void ClearDeviationAlert(string userId, string goalId) =>
        database.WriteNonLedger(userId, sql => sql.Execute(
            "UPDATE user_alert_center SET active=false WHERE user_id=$1 AND kind='goal_deviation' AND subject=$2 AND active=true",
            userId, goalId));
}
