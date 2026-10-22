using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>
/// batch2 任务5：智能定投计划仓储（smart_dca_plans，估值驱动；与 0004 均线版 dca_plans 并存）。
/// rules_json 口径：{"low_pct_boost": 低估加投倍数(>=0), "high_pct_cut": 高估削减比例(0-1), "halt_pct": 暂停阈值(0-1)}。
/// </summary>
public sealed class SmartDcaRepository(IAccountDatabase database)
{
    private const string Columns = "id,symbol,base_amount,rules_json,frequency,next_run_date,status,created_at,updated_at";

    public static void ValidateRules(JsonObject rules)
    {
        var boost = Number(rules["low_pct_boost"], "low_pct_boost");
        var cut = Number(rules["high_pct_cut"], "high_pct_cut");
        var halt = Number(rules["halt_pct"], "halt_pct");
        if (boost is < 0 or > 5) throw new ArgumentException("low_pct_boost 须在 0 到 5 之间。");
        if (cut is < 0 or >= 1) throw new ArgumentException("high_pct_cut 须在 0 到 1 之间（不含 1）。");
        if (halt is <= 0 or > 1) throw new ArgumentException("halt_pct 须在 0 到 1 之间。");
    }

    private static double Number(JsonNode? node, string key) =>
        node is JsonValue value && value.TryGetValue<double>(out var number)
            ? number
            : throw new ArgumentException($"rules 缺少有效数值字段 {key}。");

    public async Task<JsonObject> CreateAsync(string userId, string symbol, decimal baseAmount, JsonObject rules,
        string frequency, DateOnly? nextRunDate, CancellationToken cancellation = default)
    {
        if (string.IsNullOrWhiteSpace(symbol) || symbol.Length > 32) throw new ArgumentException("标的代码无效。");
        if (baseAmount is <= 0 or > 1_000_000_000m) throw new ArgumentException("基准金额须大于 0。");
        if (frequency is not ("weekly" or "monthly")) throw new ArgumentException("frequency 须为 weekly 或 monthly。");
        ValidateRules(rules);
        var id = Guid.NewGuid().ToString("N");
        return await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var rows = await sql.QueryAsync($"""
                INSERT INTO smart_dca_plans(id,user_id,symbol,base_amount,rules_json,frequency,next_run_date,created_at,updated_at)
                VALUES($1,$2,$3,$4,$5::jsonb,$6,$7,now(),now()) RETURNING {Columns}
                """, id, userId, symbol.Trim(), baseAmount, rules.ToJsonString(), frequency, nextRunDate);
            return View(rows[0]!.AsObject());
        }, cancellation);
    }

    public async Task<JsonArray> ListAsync(string userId, string? status, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql => new JsonArray(
            (await sql.QueryAsync($"SELECT {Columns} FROM smart_dca_plans WHERE user_id=$1 AND ($2::text IS NULL OR status=$2) ORDER BY created_at DESC",
                userId, status)).OfType<JsonObject>().Select(row => (JsonNode)View(row)).ToArray()), cancellation);

    public async Task<JsonObject> GetAsync(string userId, string planId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
            (await sql.QueryAsync($"SELECT {Columns} FROM smart_dca_plans WHERE user_id=$1 AND id=$2", userId, planId))
                .FirstOrDefault()?.AsObject() is { } row ? View(row)
                : throw new ApplicationFailure(ErrorCodes.SmartDcaPlanNotFound, 404, ErrorMessages.Get(ErrorCodes.SmartDcaPlanNotFound)), cancellation);

    public async Task<JsonObject> UpdateAsync(string userId, string planId, JsonObject? rules, string? frequency,
        string? status, DateOnly? nextRunDate, CancellationToken cancellation = default)
    {
        if (rules is not null) ValidateRules(rules);
        if (frequency is not (null or "weekly" or "monthly")) throw new ArgumentException("frequency 须为 weekly 或 monthly。");
        if (status is not (null or "active" or "paused" or "finished")) throw new ArgumentException("status 须为 active/paused/finished。");
        return await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var rows = await sql.QueryAsync($"""
                UPDATE smart_dca_plans SET rules_json=COALESCE($3::jsonb,rules_json),frequency=COALESCE($4,frequency),
                    status=COALESCE($5,status),next_run_date=COALESCE($6,next_run_date),updated_at=now()
                WHERE user_id=$1 AND id=$2 RETURNING {Columns}
                """, userId, planId, rules?.ToJsonString(), frequency, status, nextRunDate);
            return rows.FirstOrDefault()?.AsObject() is { } row ? View(row)
                : throw new ApplicationFailure(ErrorCodes.SmartDcaPlanNotFound, 404, ErrorMessages.Get(ErrorCodes.SmartDcaPlanNotFound));
        }, cancellation);
    }

    public async Task DeleteAsync(string userId, string planId, CancellationToken cancellation = default)
    {
        var changed = await database.WriteNonLedgerAsync(userId,
            sql => sql.ExecuteAsync("DELETE FROM smart_dca_plans WHERE user_id=$1 AND id=$2", userId, planId), cancellation);
        if (changed == 0) throw new ApplicationFailure(ErrorCodes.SmartDcaPlanNotFound, 404, ErrorMessages.Get(ErrorCodes.SmartDcaPlanNotFound));
    }

    /// <summary>到期检查用：所有活跃且 next_run_date <= today 的计划。</summary>
    public async Task<JsonArray> DuePlansAsync(DateOnly today, CancellationToken cancellation = default) =>
        ReminderViews(await database.QueryAsync(
            $"SELECT {Columns},user_id FROM smart_dca_plans WHERE status='active' AND next_run_date IS NOT NULL AND next_run_date<=$1 AND user_id IN (SELECT id FROM users WHERE status='active')",
            [today], cancellation));

    /// <summary>估值触发用：所有活跃计划去重标的。</summary>
    public async Task<JsonArray> ActivePlansAsync(CancellationToken cancellation = default) =>
        ReminderViews(await database.QueryAsync($"SELECT {Columns},user_id FROM smart_dca_plans WHERE status='active' AND user_id IN (SELECT id FROM users WHERE status='active')", [], cancellation));

    private static JsonArray ReminderViews(JsonArray rows) => new(rows.OfType<JsonObject>().Select(row =>
    {
        var view = View(row);
        view["user_id"] = row["user_id"]?.DeepClone();
        return (JsonNode)view;
    }).ToArray());

    public async Task AdvanceNextRunAsync(string userId, string planId, DateOnly next, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId,
            sql => sql.ExecuteAsync("UPDATE smart_dca_plans SET next_run_date=$3,updated_at=now() WHERE user_id=$1 AND id=$2", userId, planId, next), cancellation);

    public static JsonObject View(JsonObject row) => new()
    {
        ["id"] = row["id"]?.GetValue<string>(),
        ["symbol"] = row["symbol"]?.GetValue<string>(),
        ["base_amount"] = row["base_amount"]?.GetValue<string>(),
        ["rules"] = row["rules_json"]?.DeepClone() ?? new JsonObject(),
        ["frequency"] = row["frequency"]?.GetValue<string>(),
        ["next_run_date"] = row["next_run_date"]?.GetValue<string>(),
        ["status"] = row["status"]?.GetValue<string>(),
        ["created_at"] = row["created_at"]?.GetValue<string>(),
        ["updated_at"] = row["updated_at"]?.GetValue<string>(),
    };
}
