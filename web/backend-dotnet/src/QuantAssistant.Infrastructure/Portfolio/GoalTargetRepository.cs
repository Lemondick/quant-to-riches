using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>
/// batch5 任务1：目标规划仓储（goal_targets）。
/// 进度偏离口径：线性假设下，预期完成度 = 已流逝天数/总天数，实际完成度 = current_amount/target_amount；
/// 两者绝对差超过阈值即视为偏离（告警由 GoalProjectionService 写入 user_alert_center）。
/// </summary>
public sealed class GoalTargetRepository(IAccountDatabase database)
{
    private const string Columns =
        "id,name,target_amount,target_date,current_amount,assumed_annual_return,created_at,updated_at";

    private static JsonObject View(JsonObject row)
    {
        row["target_amount"] = row["target_amount"]?.GetValue<string>() ?? "0";
        row["current_amount"] = row["current_amount"]?.GetValue<string>() ?? "0";
        row["assumed_annual_return"] = row["assumed_annual_return"]?.GetValue<string>() ?? "0.06";
        row["target_date"] = row["target_date"]?.GetValue<string>();
        row["created_at"] = row["created_at"]?.GetValue<string>();
        row["updated_at"] = row["updated_at"]?.GetValue<string>();
        row.Remove("user_id");
        return row;
    }

    public async Task<JsonObject> CreateAsync(string userId, string name, decimal targetAmount,
        DateOnly targetDate, decimal currentAmount, double assumedAnnualReturn,
        CancellationToken cancellation = default)
    {
        Validate(name, targetAmount, targetDate, currentAmount, assumedAnnualReturn);
        var id = Guid.NewGuid().ToString("N");
        return await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var rows = await sql.QueryAsync($"""
                INSERT INTO goal_targets(id,user_id,name,target_amount,target_date,current_amount,assumed_annual_return,created_at,updated_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,now(),now()) RETURNING {Columns}
                """, id, userId, name.Trim(), targetAmount, targetDate,
                currentAmount, assumedAnnualReturn.ToString(CultureInfo.InvariantCulture));
            return View(rows[0]!.AsObject());
        }, cancellation);
    }

    public async Task<JsonArray> ListAsync(string userId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
            await sql.QueryAsync($"SELECT {Columns} FROM goal_targets WHERE user_id=$1 ORDER BY target_date",
                userId), cancellation);

    public async Task<JsonObject> GetAsync(string userId, string goalId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
            (await sql.QueryAsync($"SELECT {Columns} FROM goal_targets WHERE user_id=$1 AND id=$2", userId, goalId))
                .FirstOrDefault()?.AsObject() is { } row ? View(row)
                : throw new ApplicationFailure(ErrorCodes.GoalTargetNotFound, 404, ErrorMessages.Get(ErrorCodes.GoalTargetNotFound)), cancellation);

    public async Task<JsonObject> UpdateAsync(string userId, string goalId, string? name, decimal? targetAmount,
        DateOnly? targetDate, double? assumedAnnualReturn, CancellationToken cancellation = default)
    {
        if (name is not null && string.IsNullOrWhiteSpace(name)) throw new ArgumentException("目标名称不能为空。");
        if (targetAmount is <= 0) throw new ArgumentException("目标金额须大于 0。");
        if (assumedAnnualReturn is < 0 or >= 2) throw new ArgumentException("假设年化须在 0 到 2 之间。");
        if (targetDate is { } date && date <= DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)))
            throw new ArgumentException("目标日期须晚于今天。");
        return await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var rows = await sql.QueryAsync($"""
                UPDATE goal_targets SET name=COALESCE($3,name),target_amount=COALESCE($4,target_amount),
                    target_date=COALESCE($5,target_date),assumed_annual_return=COALESCE($6::numeric,assumed_annual_return),
                    updated_at=now()
                WHERE user_id=$1 AND id=$2 RETURNING {Columns}
                """, userId, goalId, name?.Trim(), targetAmount, targetDate,
                assumedAnnualReturn?.ToString(CultureInfo.InvariantCulture));
            return rows.FirstOrDefault()?.AsObject() is { } row ? View(row)
                : throw new ApplicationFailure(ErrorCodes.GoalTargetNotFound, 404, ErrorMessages.Get(ErrorCodes.GoalTargetNotFound));
        }, cancellation);
    }

    /// <summary>投影前刷新 current_amount（账本净资产取值）。</summary>
    public async Task<decimal> RefreshCurrentAmountAsync(string userId, string goalId, decimal netWorth,
        CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var rows = await sql.QueryAsync(
                "UPDATE goal_targets SET current_amount=$3,updated_at=now() WHERE user_id=$1 AND id=$2 RETURNING current_amount",
                userId, goalId, netWorth);
            return rows.FirstOrDefault()?.AsObject() is { } row
                ? decimal.Parse(row["current_amount"]!.GetValue<string>(), CultureInfo.InvariantCulture)
                : throw new ApplicationFailure(ErrorCodes.GoalTargetNotFound, 404, ErrorMessages.Get(ErrorCodes.GoalTargetNotFound));
        }, cancellation);

    public async Task DeleteAsync(string userId, string goalId, CancellationToken cancellation = default)
    {
        var changed = await database.WriteNonLedgerAsync(userId, async sql =>
            await sql.ExecuteAsync("DELETE FROM goal_targets WHERE user_id=$1 AND id=$2", userId, goalId), cancellation);
        if (changed == 0)
            throw new ApplicationFailure(ErrorCodes.GoalTargetNotFound, 404, ErrorMessages.Get(ErrorCodes.GoalTargetNotFound));
    }

    private static void Validate(string name, decimal targetAmount, DateOnly targetDate, decimal currentAmount, double assumedAnnualReturn)
    {
        if (string.IsNullOrWhiteSpace(name) || name.Trim().Length > 100) throw new ArgumentException("目标名称须为 1-100 个字符。");
        if (targetAmount is <= 0 or > 1_000_000_000_000m) throw new ArgumentException("目标金额须大于 0。");
        if (currentAmount is < 0 or > 1_000_000_000_000m) throw new ArgumentException("当前金额不能为负。");
        if (assumedAnnualReturn is < 0 or >= 2) throw new ArgumentException("假设年化须在 0 到 2 之间。");
        if (targetDate <= DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8))) throw new ArgumentException("目标日期须晚于今天。");
    }
}
