using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>E5：资金计划仓储（原 PlanningController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class CashFlowPlanRepository(IAccountDatabase database)
{
    private const string Columns = "plan_id,name,direction,amount,frequency,start_date,end_date,status,note,created_at,updated_at";

    public async Task<JsonObject> ListAsync(string userId, string? status, int limit, int offset, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
        {
            var total = Convert.ToInt64(await sql.ScalarAsync("SELECT count(*) FROM cash_flow_plans WHERE user_id=$1 AND ($2::text IS NULL OR status=$2)", userId, status));
            var rows = await sql.QueryAsync($"SELECT {Columns} FROM cash_flow_plans WHERE user_id=$1 AND ($2::text IS NULL OR status=$2) ORDER BY start_date,plan_id LIMIT $3 OFFSET $4", userId, status, limit, offset);
            foreach (var row in rows.OfType<JsonObject>()) Preview(row);
            return new JsonObject
            {
                ["items"] = rows, ["total"] = total, ["offset"] = offset, ["limit"] = limit,
                ["has_more"] = (long)offset + rows.Count < total,
            };
        }, cancellation);

    public async Task<JsonObject> CreateAsync(string userId, string name, string direction, decimal amount, string frequency,
        DateOnly startDate, DateOnly? endDate, string? note, string? planId, CancellationToken cancellation = default)
    {
        var id = planId ?? Guid.NewGuid().ToString("N");
        return await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var existing = (await sql.QueryAsync($"SELECT {Columns} FROM cash_flow_plans WHERE user_id=$1 AND plan_id=$2", userId, id)).FirstOrDefault()?.AsObject();
            if (existing is not null)
            {
                if (existing["name"]!.GetValue<string>() != name || existing["direction"]!.GetValue<string>() != direction
                    || decimal.Parse(existing["amount"]!.GetValue<string>(), CultureInfo.InvariantCulture) != amount
                    || existing["frequency"]!.GetValue<string>() != frequency
                    || existing["start_date"]!.GetValue<string>() != startDate.ToString("yyyy-MM-dd")
                    || existing["end_date"]?.GetValue<string>() != endDate?.ToString("yyyy-MM-dd")
                    || existing["note"]?.GetValue<string>() != note)
                    throw new ApplicationFailure("cash_flow_plan_exists", 409, "此计划编号已存在，请读取已有计划后编辑");
                return Preview(existing);
            }
            return Preview((await sql.QueryAsync($"""
                INSERT INTO cash_flow_plans(user_id,plan_id,name,direction,amount,frequency,start_date,end_date,status,note,created_at,updated_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,'active',$9,now(),now()) RETURNING {Columns}
                """, userId, id, name, direction, amount, frequency, startDate, endDate, note))[0]!.AsObject());
        }, cancellation);
    }

    public async Task<JsonObject> UpdateAsync(string userId, string planId, string name, string direction, decimal amount,
        string frequency, DateOnly startDate, DateOnly? endDate, string? note, string status, DateTimeOffset expectedUpdatedAt,
        CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            await RequireCurrentAsync(sql, userId, planId, expectedUpdatedAt);
            return Preview((await sql.QueryAsync($"""
                UPDATE cash_flow_plans SET name=$3,direction=$4,amount=$5,frequency=$6,start_date=$7,end_date=$8,note=$9,status=$10,
                  updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                WHERE user_id=$1 AND plan_id=$2 RETURNING {Columns}
                """, userId, planId, name, direction, amount, frequency, startDate, endDate, note, status))[0]!.AsObject());
        }, cancellation);

    public async Task<JsonObject> SetStatusAsync(string userId, string planId, string status, DateTimeOffset expectedUpdatedAt,
        CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            await RequireCurrentAsync(sql, userId, planId, expectedUpdatedAt);
            return Preview((await sql.QueryAsync($"""
                UPDATE cash_flow_plans SET status=$3,updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                WHERE user_id=$1 AND plan_id=$2 RETURNING {Columns}
                """, userId, planId, status))[0]!.AsObject());
        }, cancellation);

    public async Task<JsonObject> DueAsync(string userId, DateOnly target, CancellationToken cancellation = default)
    {
        var plans = await database.ReadAsync(sql =>
            sql.QueryAsync($"SELECT {Columns} FROM cash_flow_plans WHERE user_id=$1 AND status='active' AND start_date<=$2 AND (end_date IS NULL OR end_date>=$2) ORDER BY start_date,plan_id", userId, target), cancellation);
        var items = new JsonArray();
        foreach (var plan in plans.OfType<JsonObject>())
        {
            if (!IsDue(plan, target)) continue;
            var item = new JsonObject { ["scheduled_on"] = target.ToString("yyyy-MM-dd") };
            foreach (var key in new[] { "plan_id", "name", "direction", "amount", "note" }) item[key] = plan[key]?.DeepClone();
            items.Add(item);
        }
        return new JsonObject { ["items"] = items };
    }

    private static async Task RequireCurrentAsync(IAccountSql sql, string userId, string id, DateTimeOffset expected)
    {
        var row = (await sql.QueryAsync("SELECT updated_at FROM cash_flow_plans WHERE user_id=$1 AND plan_id=$2 FOR UPDATE", userId, id)).FirstOrDefault()?.AsObject()
            ?? throw new ApplicationFailure("cash_flow_plan_not_found", 404, "资金计划不存在");
        if (DateTimeOffset.Parse(row["updated_at"]!.GetValue<string>(), CultureInfo.InvariantCulture) != expected)
            throw new ApplicationFailure("cash_flow_plan_changed", 409, "计划已被修改，请重新读取后编辑");
    }

    private static JsonObject Preview(JsonObject row)
    {
        var start = DateOnly.Parse(row["start_date"]!.GetValue<string>());
        row["next_dates"] = Dates(row, start.AddDays(Math.Min(365, DateOnly.MaxValue.DayNumber - start.DayNumber)), 12);
        return row;
    }

    private static JsonArray Dates(JsonObject plan, DateOnly until, int limit)
    {
        var start = DateOnly.Parse(plan["start_date"]!.GetValue<string>());
        var end = plan["end_date"] is null ? until : DateOnly.Parse(plan["end_date"]!.GetValue<string>());
        var frequency = plan["frequency"]!.GetValue<string>();
        var values = new JsonArray();
        if (plan["status"]!.GetValue<string>() != "active") return values;
        for (var index = 0; values.Count < limit; index++)
        {
            var monthStep = frequency switch { "monthly" => 1, "quarterly" => 3, "annual" => 12, _ => 0 };
            if (frequency == "weekly" && 7 * index > DateOnly.MaxValue.DayNumber - start.DayNumber
                || monthStep > 0 && monthStep * index > (9999 - start.Year) * 12 + 12 - start.Month) break;
            var current = frequency switch
            { "weekly" => start.AddDays(7 * index), "monthly" => start.AddMonths(index), "quarterly" => start.AddMonths(3 * index), "annual" => start.AddYears(index), _ => start };
            if (current > until || current > end) break;
            values.Add(current.ToString("yyyy-MM-dd"));
            if (frequency == "once") break;
        }
        return values;
    }

    private static bool IsDue(JsonObject plan, DateOnly target)
    {
        var start = DateOnly.Parse(plan["start_date"]!.GetValue<string>());
        var months = (target.Year - start.Year) * 12 + target.Month - start.Month;
        return plan["frequency"]!.GetValue<string>() switch
        {
            "once" => target == start,
            "weekly" => (target.DayNumber - start.DayNumber) % 7 == 0,
            "monthly" => start.AddMonths(months) == target,
            "quarterly" => months % 3 == 0 && start.AddMonths(months) == target,
            "annual" => months % 12 == 0 && start.AddMonths(months) == target,
            _ => false,
        };
    }
}
