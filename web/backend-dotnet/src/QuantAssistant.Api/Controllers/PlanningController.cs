using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/planning/cash-flows")]
public sealed class PlanningController(AccountDatabase database) : ControllerBase
{
    public sealed record PlanBody(string Name, string Direction, decimal Amount, string Frequency, [property: JsonRequired] DateOnly StartDate,
        DateOnly? EndDate = null, string? Note = null, string? PlanId = null);
    public sealed record UpdateBody(string Name, string Direction, decimal Amount, string Frequency,
        [property: JsonRequired] DateOnly StartDate, [property: JsonRequired] DateTimeOffset ExpectedUpdatedAt,
        DateOnly? EndDate = null, string? Note = null, string Status = "active");
    public sealed record StatusBody(string Status, [property: JsonRequired] DateTimeOffset ExpectedUpdatedAt);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private const string Columns = "plan_id,name,direction,amount,frequency,start_date,end_date,status,note,created_at,updated_at";

    [HttpGet]
    public IActionResult List([FromQuery] string? status = null, [FromQuery] int limit = 100, [FromQuery] int offset = 0)
    {
        if (limit is < 1 or > 500 || offset < 0 || status is not (null or "active" or "paused" or "completed")) throw new ArgumentException("计划状态或分页参数无效");
        return Ok(database.Read(sql =>
        {
            var total = Convert.ToInt64(sql.Scalar("SELECT count(*) FROM cash_flow_plans WHERE user_id=$1 AND ($2::text IS NULL OR status=$2)", UserId, status));
            var rows = sql.Query($"SELECT {Columns} FROM cash_flow_plans WHERE user_id=$1 AND ($2::text IS NULL OR status=$2) ORDER BY start_date,plan_id LIMIT $3 OFFSET $4", UserId, status, limit, offset);
            foreach (var row in rows.OfType<JsonObject>()) Preview(row);
            return new { items = rows, total, offset, limit, has_more = (long)offset + rows.Count < total };
        }));
    }

    [HttpPost]
    public IActionResult Create([FromBody] PlanBody body)
    {
        body = Validate(body);
        var id = body.PlanId ?? Guid.NewGuid().ToString("N");
        var row = database.Write(UserId, sql =>
        {
            var existing = sql.Query($"SELECT {Columns} FROM cash_flow_plans WHERE user_id=$1 AND plan_id=$2", UserId, id).FirstOrDefault()?.AsObject();
            if (existing is not null)
            {
                if (existing["name"]!.GetValue<string>() != body.Name || existing["direction"]!.GetValue<string>() != body.Direction
                    || decimal.Parse(existing["amount"]!.GetValue<string>(), CultureInfo.InvariantCulture) != body.Amount
                    || existing["frequency"]!.GetValue<string>() != body.Frequency
                    || existing["start_date"]!.GetValue<string>() != body.StartDate.ToString("yyyy-MM-dd")
                    || existing["end_date"]?.GetValue<string>() != body.EndDate?.ToString("yyyy-MM-dd")
                    || existing["note"]?.GetValue<string>() != body.Note)
                    throw new ApplicationFailure("cash_flow_plan_exists", 409, "此计划编号已存在，请读取已有计划后编辑");
                return Preview(existing);
            }
            return Preview(sql.Query($"""
                INSERT INTO cash_flow_plans(user_id,plan_id,name,direction,amount,frequency,start_date,end_date,status,note,created_at,updated_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,'active',$9,now(),now()) RETURNING {Columns}
                """, UserId, id, body.Name, body.Direction, body.Amount, body.Frequency, body.StartDate, body.EndDate, body.Note)[0]!.AsObject());
        });
        return Ok(row);
    }

    [HttpPut("{plan_id}")]
    public IActionResult Update(string plan_id, [FromBody] UpdateBody body)
    {
        var plan = Validate(new PlanBody(body.Name, body.Direction, body.Amount, body.Frequency, body.StartDate, body.EndDate, body.Note));
        ValidateStatus(body.Status);
        return Ok(database.Write(UserId, sql =>
        {
            RequireCurrent(sql, plan_id, body.ExpectedUpdatedAt);
            return Preview(sql.Query($"""
                UPDATE cash_flow_plans SET name=$3,direction=$4,amount=$5,frequency=$6,start_date=$7,end_date=$8,note=$9,status=$10,
                  updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                WHERE user_id=$1 AND plan_id=$2 RETURNING {Columns}
                """, UserId, plan_id, plan.Name, plan.Direction, plan.Amount, plan.Frequency, plan.StartDate, plan.EndDate, plan.Note, body.Status)[0]!.AsObject());
        }));
    }

    [HttpPatch("{plan_id}/status")]
    public IActionResult SetStatus(string plan_id, [FromBody] StatusBody body)
    {
        ValidateStatus(body.Status);
        return Ok(database.Write(UserId, sql =>
        {
            RequireCurrent(sql, plan_id, body.ExpectedUpdatedAt);
            return Preview(sql.Query($"""
                UPDATE cash_flow_plans SET status=$3,updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                WHERE user_id=$1 AND plan_id=$2 RETURNING {Columns}
                """, UserId, plan_id, body.Status)[0]!.AsObject());
        }));
    }

    private void RequireCurrent(AccountSql sql, string id, DateTimeOffset expected)
    {
        var row = sql.Query("SELECT updated_at FROM cash_flow_plans WHERE user_id=$1 AND plan_id=$2 FOR UPDATE", UserId, id).FirstOrDefault()?.AsObject()
            ?? throw new ApplicationFailure("cash_flow_plan_not_found", 404, "资金计划不存在");
        if (DateTimeOffset.Parse(row["updated_at"]!.GetValue<string>(), CultureInfo.InvariantCulture) != expected)
            throw new ApplicationFailure("cash_flow_plan_changed", 409, "计划已被修改，请重新读取后编辑");
    }

    private static void ValidateStatus(string status)
    {
        if (status is not ("active" or "paused" or "completed")) throw new ArgumentException("资金计划状态无效");
    }

    private static PlanBody Validate(PlanBody body)
    {
        if (string.IsNullOrWhiteSpace(body.Name) || body.Name.Length > 200 || body.Name.Any(char.IsControl)
            || body.Direction is not ("deposit" or "withdrawal")
            || body.Frequency is not ("once" or "weekly" or "monthly" or "quarterly" or "annual")
            || body.Amount <= 0 || body.Amount >= 100000000000000000000m || decimal.Round(body.Amount, 8) != body.Amount
            || body.EndDate < body.StartDate || body.Note?.Length > 500
            || body.PlanId is not null && (string.IsNullOrWhiteSpace(body.PlanId) || body.PlanId.Length > 128 || body.PlanId.Any(char.IsControl)))
            throw new ArgumentException("资金计划参数无效");
        return body with { Name = body.Name.Trim(), Note = string.IsNullOrWhiteSpace(body.Note) ? null : body.Note.Trim(), PlanId = body.PlanId?.Trim() };
    }

    private static JsonObject Preview(JsonObject row)
    {
        var start = DateOnly.Parse(row["start_date"]!.GetValue<string>());
        row["next_dates"] = Dates(row, start.AddDays(Math.Min(365, DateOnly.MaxValue.DayNumber - start.DayNumber)), 12);
        return row;
    }

    [HttpGet("due")]
    public IActionResult Due([FromQuery(Name = "as_of")] DateOnly? asOf = null)
    {
        var target = asOf ?? DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        var plans = database.Read(sql => sql.Query($"SELECT {Columns} FROM cash_flow_plans WHERE user_id=$1 AND status='active' AND start_date<=$2 AND (end_date IS NULL OR end_date>=$2) ORDER BY start_date,plan_id", UserId, target));
        var items = new JsonArray();
        foreach (var plan in plans.OfType<JsonObject>())
        {
            if (!IsDue(plan, target)) continue;
            var item = new JsonObject { ["scheduled_on"] = target.ToString("yyyy-MM-dd") };
            foreach (var key in new[] { "plan_id", "name", "direction", "amount", "note" }) item[key] = plan[key]?.DeepClone();
            items.Add(item);
        }
        return Ok(new { items });
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
