using System.Text.Json;
using System.Text.Json.Nodes;
using System.Security.Cryptography;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Runtime;

public sealed class TradingCalendarRepository(AccountDatabase database)
{
    public sealed record Calendar(int Year, string Source, string[] Holidays);
    public JsonObject Save(string actor, Calendar body, string? expectedRevision = null)
    {
        if (body.Year is < 2000 or > 2100 || !Uri.TryCreate(body.Source, UriKind.Absolute, out var url) || url.Scheme != "https"
            || url.UserInfo.Length > 0 || !new[] { "sse.com.cn", "szse.cn", "bse.cn" }.Any(d => url.Host == d || url.Host.EndsWith("." + d, StringComparison.OrdinalIgnoreCase))
            || body.Source?.Length > 2000 || body.Holidays is null || body.Holidays.Length is < 1 or > 100
            || body.Holidays.Any(d => !DateOnly.TryParseExact(d, "yyyy-MM-dd", out var date) || date.Year != body.Year))
            throw new ArgumentException("请提供交易所官方公告和属于所选年份的休市日期");
        body = body with { Holidays = body.Holidays.Distinct().Order().ToArray() };
        return database.Write(actor, sql =>
        {
            sql.Execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "calendar:" + body.Year);
            var existing = View(sql, body.Year);
            if (expectedRevision is not null && expectedRevision != existing["revision"]!.GetValue<string>())
                throw new ApplicationFailure("calendar_changed", 409, "此年份日历已被修改，请重新读取后保存");
            sql.Execute("INSERT INTO site_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", "calendar:" + body.Year, JsonSerializer.Serialize(body, FinanceJson.Options));
            sql.Execute("INSERT INTO admin_audit(actor_id,action,target_id,detail,created_at) VALUES($1,'calendar',NULL,$2,now())", actor,
                new JsonObject { ["value"] = new JsonObject { ["year"] = body.Year, ["source"] = body.Source, ["holiday_count"] = body.Holidays.Length } }.ToJsonString());
            return View(sql, body.Year);
        });
    }

    public JsonObject View(int year)
    {
        if (year is < 2000 or > 2100) throw new ArgumentException("日历年份须为2000至2100");
        return database.Read(sql => View(sql, year));
    }

    private static JsonObject View(AccountSql sql, int year)
    {
        var value = sql.Scalar("SELECT value FROM site_settings WHERE key=$1", "calendar:" + year) as string;
        var calendar = value is null ? Default(year) : JsonSerializer.Deserialize<Calendar>(value, FinanceJson.Options);
        var revision = calendar is null ? "missing" : Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(value ?? JsonSerializer.Serialize(calendar, FinanceJson.Options)))).ToLowerInvariant();
        return new JsonObject { ["year"] = year, ["configured"] = calendar is not null,
            ["source"] = calendar?.Source, ["holidays"] = new JsonArray(calendar?.Holidays.Select(day => (JsonNode?)JsonValue.Create(day)).ToArray() ?? []),
            ["revision"] = revision, ["origin"] = value is not null ? "imported" : calendar is null ? "missing" : "builtin" };
    }

    public JsonObject Status(DateOnly today)
    {
        var current = View(today.Year);
        var next = View(today.Year + 1);
        var warnings = new JsonArray();
        if (!current["configured"]!.GetValue<bool>()) warnings.Add($"{today.Year}年日历缺失，自动报告暂停，请导入交易所官方休市日期。");
        var prepare = today.Month >= 10;
        if (prepare && !next["configured"]!.GetValue<bool>()) warnings.Add($"请准备{today.Year + 1}年交易日历，官方公告发布后导入；未导入前跨年自动报告会暂停。");
        return new JsonObject { ["current"] = current, ["next"] = next, ["prepare_next_year"] = prepare, ["warnings"] = warnings };
    }

    public Calendar? Load(int year) => database.Read(sql => sql.Scalar("SELECT value FROM site_settings WHERE key=$1", "calendar:" + year) is string value
        ? JsonSerializer.Deserialize<Calendar>(value, FinanceJson.Options) : Default(year));
    public bool? TradingDay(DateOnly day)
    {
        var calendar = Load(day.Year);
        return calendar is null ? null : day.DayOfWeek is not (DayOfWeek.Saturday or DayOfWeek.Sunday) && !calendar.Holidays.Contains(day.ToString("yyyy-MM-dd"));
    }
    public Task<bool?> TradingDayAsync(DateOnly day, CancellationToken cancellation = default) => database.ReadAsync(async sql =>
    {
        var raw = await sql.ScalarAsync("SELECT value FROM site_settings WHERE key=$1", "calendar:" + day.Year) as string;
        var calendar = raw is null ? Default(day.Year) : JsonSerializer.Deserialize<Calendar>(raw, FinanceJson.Options);
        return calendar is null ? (bool?)null : day.DayOfWeek is not (DayOfWeek.Saturday or DayOfWeek.Sunday) && !calendar.Holidays.Contains(day.ToString("yyyy-MM-dd"));
    }, cancellation);
    private static Calendar? Default(int year)
    {
        if (year != 2026) return null;
        var days = new List<string>();
        foreach (var (start, end) in new[] { ("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"), ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"), ("10-01", "10-07") })
            for (var date = DateOnly.Parse("2026-" + start); date <= DateOnly.Parse("2026-" + end); date = date.AddDays(1)) days.Add(date.ToString("yyyy-MM-dd"));
        return new(year, "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml", days.ToArray());
    }
}
