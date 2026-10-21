using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>
/// batch5 任务4：定投执行登记仓储（dca_executions，FK→smart_dca_plans）。
/// 计划 vs 执行口径：以 plan.created_at 为锚点、按 frequency（weekly/monthly）推算应投期；
/// 每期 planned_amount 取 plan.base_amount。只记录与提醒，不自动扣款、不执行真实交易。
/// </summary>
public sealed class DcaExecutionRepository(IAccountDatabase database)
{
    private const string Columns =
        "e.id,e.plan_id,e.due_date::text AS due_date,e.executed_at::text AS executed_at,e.planned_amount::text AS planned_amount,e.actual_amount::text AS actual_amount,e.status,e.note,e.created_at::text AS created_at";

    /// <summary>INSERT…RETURNING 用的列清单（无表别名）。</summary>
    private const string InsertColumns =
        "id,plan_id,due_date::text AS due_date,executed_at::text AS executed_at,planned_amount::text AS planned_amount,actual_amount::text AS actual_amount,status,note,created_at::text AS created_at";

    private static JsonObject View(JsonObject row)
    {
        row["executed_at"] = row["executed_at"]?.GetValue<string>();
        row["created_at"] = row["created_at"]?.GetValue<string>();
        return row;
    }

    /// <summary>登记一次实际扣款（status=done；同 plan+due_date 幂等：冲突时更新实际金额/时间）。</summary>
    public async Task<JsonObject> RecordAsync(string userId, string planId, DateOnly dueDate,
        decimal actualAmount, DateTimeOffset? executedAt, string? note, CancellationToken cancellation = default)
    {
        if (actualAmount is <= 0 or > 1_000_000_000m) throw new ArgumentException("实际扣款金额须大于 0。");
        if (dueDate > DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)).AddDays(1))
            throw new ArgumentException("应投日期不能晚于明天。");
        if (note is { Length: > 500 }) throw new ArgumentException("备注过长（最多 500 字）。");
        var plan = await PlanAsync(userId, planId, cancellation);
        var planned = decimal.Parse(plan["base_amount"]!.GetValue<string>(), CultureInfo.InvariantCulture);
        return await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var id = Guid.NewGuid().ToString("N");
            var rows = await sql.QueryAsync($"""
                INSERT INTO dca_executions(id,plan_id,due_date,executed_at,planned_amount,actual_amount,status,note,created_at,updated_at)
                VALUES($1,$2,$3,$4,$5,$6,'done',$7,now(),now())
                ON CONFLICT(plan_id,due_date) DO UPDATE SET
                    executed_at=COALESCE(EXCLUDED.executed_at,dca_executions.executed_at),
                    actual_amount=EXCLUDED.actual_amount, status='done',
                    note=COALESCE(EXCLUDED.note,dca_executions.note), updated_at=now()
                RETURNING {InsertColumns}
                """, id, planId, dueDate, executedAt?.UtcDateTime, planned, actualAmount, note);
            return View(rows[0]!.AsObject());
        }, cancellation);
    }

    /// <summary>按期标记 skipped（主动跳过本期，不计入漏投）。</summary>
    public async Task<JsonObject> SkipAsync(string userId, string planId, DateOnly dueDate, string? note,
        CancellationToken cancellation = default)
    {
        await PlanAsync(userId, planId, cancellation);
        return await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var id = Guid.NewGuid().ToString("N");
            var planned = Convert.ToDecimal(await sql.ScalarAsync(
                "SELECT base_amount FROM smart_dca_plans WHERE user_id=$1 AND id=$2", userId, planId));
            var rows = await sql.QueryAsync($"""
                INSERT INTO dca_executions(id,plan_id,due_date,planned_amount,status,note,created_at,updated_at)
                VALUES($1,$2,$3,$4,'skipped',$5,now(),now())
                ON CONFLICT(plan_id,due_date) DO UPDATE SET status='skipped',
                    note=COALESCE(EXCLUDED.note,dca_executions.note), updated_at=now()
                RETURNING {InsertColumns}
                """, id, planId, dueDate, planned, note);
            return View(rows[0]!.AsObject());
        }, cancellation);
    }

    public async Task<JsonArray> ExecutionsAsync(string userId, string? planId, CancellationToken cancellation = default)
    {
        var rows = await database.ReadAsync(async sql =>
            await sql.QueryAsync($"""
                SELECT {Columns} FROM dca_executions e
                JOIN smart_dca_plans p ON p.id=e.plan_id
                WHERE p.user_id=$1 AND ($2::text IS NULL OR e.plan_id=$2)
                ORDER BY e.due_date DESC
                """, userId, planId), cancellation);
        // 行已在 rows 数组内就位：就地 View 转换后直接返回，避免跨数组重挂父节点。
        foreach (var row in rows.OfType<JsonObject>()) View(row);
        return rows;
    }

    public async Task<JsonArray> ActivePlansAsync(string userId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
            await sql.QueryAsync(
                "SELECT id,symbol,base_amount::text AS base_amount,frequency,created_at::date::text AS created_at FROM smart_dca_plans WHERE user_id=$1 AND status='active' ORDER BY created_at",
                userId), cancellation);

    private async Task<JsonObject> PlanAsync(string userId, string planId, CancellationToken cancellation) =>
        await database.ReadAsync(async sql =>
            (await sql.QueryAsync(
                "SELECT id,symbol,base_amount::text AS base_amount,frequency FROM smart_dca_plans WHERE user_id=$1 AND id=$2",
                userId, planId)).FirstOrDefault()?.AsObject()
            ?? throw new ApplicationFailure(ErrorCodes.SmartDcaPlanNotFound, 404, ErrorMessages.Get(ErrorCodes.SmartDcaPlanNotFound)), cancellation);
}
