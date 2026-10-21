using System.Text.Json;
using System.Text.Json.Nodes;
using System.Security.Cryptography;
using System.Collections.Frozen;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Market;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Runtime;

public sealed class TradingCalendarRepository(AccountDatabase database, TimeProvider? clock = null, bool autoAccept = false, QuantAssistant.Infrastructure.Auth.AdminAuditRepository? auditChain = null)
{
    private readonly SemaphoreSlim snapshotGate = new(1);
    private sealed record CachedCalendar(int Year, MarketCalendars Data, DateTimeOffset Expires);
    private CachedCalendar? cached;
    private DateTimeOffset Now => (clock ?? TimeProvider.System).GetUtcNow();
    /// <summary>batch2-12：admin_audit 统一走哈希链写入；auditChain 为 null（测试构造）时退化为旧写法。</summary>
    private void WriteAudit(QuantAssistant.Core.Abstractions.IAccountSql sql, string actor, string action, string? targetId, string detail)
    {
        if (auditChain is null)
            sql.Execute("INSERT INTO admin_audit(actor_id,action,target_id,detail,created_at) VALUES($1,$2,$3,$4,now())", actor, action, targetId, detail);
        else
            auditChain.Write(sql, actor, action, targetId, detail, DateTime.UtcNow);
    }
    public MarketCalendars CurrentSnapshot => Volatile.Read(ref cached) is { } saved && saved.Expires > Now ? saved.Data : MarketCalendars.Empty;
    /// <summary>
    /// F1：日历存储形状。Origin: imported（人工导入）/ auto（worker 自动生成）；
    /// Confirmed=false 表示待管理员确认；Provenance 记录生成来源。
    /// 老行缺这些字段时按默认值反序列化（Parse 兼容）。
    /// </summary>
    public sealed record Calendar(int Year, string Source, string[] Holidays, string Market = "CN",
        string Origin = "imported", bool Confirmed = true, double? Confidence = null,
        string? Notes = null, JsonObject? Provenance = null);
    /// <summary>F1：该日历是否可用于交易日判定（未确认的自动日历仅在 autoAccept 时采纳）。</summary>
    private bool Effective(Calendar? calendar) => calendar is not null && (calendar.Confirmed || calendar.Origin != "auto" || autoAccept);
    private static string MarketId(string market)
    {
        var normalized = market?.Trim().ToUpperInvariant();
        return normalized is not null && MarketCalendars.Markets.Contains(normalized) ? normalized : throw new ArgumentException("市场须为CN、HK、US或JP");
    }
    private static string Key(string market, int year) => market == "CN" ? "calendar:" + year : $"calendar:{market}:{year}";
    private static string[] Domains(string market) => market switch
    {
        "CN" => ["sse.com.cn", "szse.cn", "bse.cn"], "HK" => ["hkex.com.hk"],
        "US" => ["nyse.com", "nasdaq.com", "cboe.com"], "JP" => ["jpx.co.jp"], _ => [],
    };
    public JsonObject Save(string actor, Calendar body, string? expectedRevision = null)
    {
        body = body with { Market = MarketId(body.Market) };
        if (body.Year is < 2000 or > 2100 || !Uri.TryCreate(body.Source, UriKind.Absolute, out var url) || url.Scheme != "https"
            || url.UserInfo.Length > 0 || !Domains(body.Market).Any(d => url.Host == d || url.Host.EndsWith("." + d, StringComparison.OrdinalIgnoreCase))
            || body.Source?.Length > 2000 || body.Holidays is null || body.Holidays.Length is < 1 or > 100
            || body.Holidays.Any(d => !DateOnly.TryParseExact(d, "yyyy-MM-dd", out var date) || date.Year != body.Year))
            throw new ArgumentException("请提供交易所官方公告和属于所选年份的休市日期");
        body = body with { Holidays = body.Holidays.Distinct().Order().ToArray() };
        var result = database.WriteNonLedger(actor, sql =>
        {
            sql.Execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", Key(body.Market, body.Year));
            var existing = View(sql, body.Year, body.Market);
            if (expectedRevision is not null && expectedRevision != existing["revision"]!.GetValue<string>())
                throw new ApplicationFailure("calendar_changed", 409, "此年份日历已被修改，请重新读取后保存");
            sql.Execute("INSERT INTO site_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", Key(body.Market, body.Year), JsonSerializer.Serialize(body, FinanceJson.Options));
            WriteAudit(sql, actor, "calendar", null,
                new JsonObject { ["value"] = new JsonObject { ["market"] = body.Market, ["year"] = body.Year, ["source"] = body.Source, ["holiday_count"] = body.Holidays.Length } }.ToJsonString());
            return View(sql, body.Year, body.Market);
        });
        // Wait out any old snapshot read, then invalidate before returning the saved revision.
        snapshotGate.Wait();
        try { Volatile.Write(ref cached, null); }
        finally { snapshotGate.Release(); }
        return result;
    }

    /// <summary>
    /// F1：保存 worker 自动生成的日历（origin=auto, confirmed=false）。
    /// 已确认的日历不会被覆盖（409）；未确认的自动日历允许重写。
    /// </summary>
    public JsonObject SaveAuto(string actor, string market, int year, string[] holidays, string source,
        double? confidence, string[]? notes, JsonObject? provenance)
    {
        market = MarketId(market);
        if (year is < 2000 or > 2100 || string.IsNullOrWhiteSpace(source) || source.Length > 2000
            || holidays is null || holidays.Length is < 1 or > 100
            || holidays.Any(d => !DateOnly.TryParseExact(d, "yyyy-MM-dd", out var date) || date.Year != year)
            || confidence is < 0 or > 1)
            throw new ArgumentException("自动生成的日历数据无效");
        var body = new Calendar(year, source.Trim(), holidays.Distinct().Order().ToArray(), market,
            Origin: "auto", Confirmed: false, Confidence: confidence,
            Notes: notes is null || notes.Length == 0 ? null : string.Join("\n", notes), Provenance: provenance);
        var result = database.WriteNonLedger(actor, sql =>
        {
            sql.Execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", Key(market, year));
            var existing = View(sql, year, market);
            if (existing["configured"]!.GetValue<bool>() && existing["confirmed"]!.GetValue<bool>())
                throw new ApplicationFailure("calendar_exists", 409, "该年份日历已存在且已确认，自动生成不会覆盖。");
            sql.Execute("INSERT INTO site_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", Key(market, year), JsonSerializer.Serialize(body, FinanceJson.Options));
            WriteAudit(sql, actor, "calendar.auto", null,
                new JsonObject { ["value"] = new JsonObject { ["market"] = market, ["year"] = year, ["source"] = body.Source, ["holiday_count"] = body.Holidays.Length, ["confidence"] = confidence } }.ToJsonString());
            return View(sql, year, market);
        });
        snapshotGate.Wait();
        try { Volatile.Write(ref cached, null); }
        finally { snapshotGate.Release(); }
        return result;
    }

    /// <summary>F1：管理员确认自动生成的日历（仅 origin=auto 且未确认的可确认；幂等）。</summary>
    public JsonObject Confirm(string actor, string market, int year)
    {
        market = MarketId(market);
        if (year is < 2000 or > 2100) throw new ArgumentException("日历年份须为2000至2100");
        var result = database.WriteNonLedger(actor, sql =>
        {
            sql.Execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", Key(market, year));
            var calendar = Parse(sql.Scalar("SELECT value FROM site_settings WHERE key=$1", Key(market, year)) as string, year, market)
                ?? throw new ApplicationFailure("calendar_not_found", 404, "该年份日历不存在。");
            if (calendar.Origin != "auto") throw new ApplicationFailure("calendar_not_auto", 409, "只有自动生成的日历需要确认。");
            if (!calendar.Confirmed)
            {
                sql.Execute("INSERT INTO site_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                    Key(market, year), JsonSerializer.Serialize(calendar with { Confirmed = true }, FinanceJson.Options));
                WriteAudit(sql, actor, "calendar.confirm", null,
                    new JsonObject { ["value"] = new JsonObject { ["market"] = market, ["year"] = year } }.ToJsonString());
            }
            return View(sql, year, market);
        });
        snapshotGate.Wait();
        try { Volatile.Write(ref cached, null); }
        finally { snapshotGate.Release(); }
        return result;
    }

    public JsonObject View(int year, string market = "CN")
    {
        if (year is < 2000 or > 2100) throw new ArgumentException("日历年份须为2000至2100");
        market = MarketId(market);
        return database.Read(sql => View(sql, year, market));
    }

    private static JsonObject View(AccountSql sql, int year, string market)
    {
        var value = sql.Scalar("SELECT value FROM site_settings WHERE key=$1", Key(market, year)) as string;
        var calendar = Parse(value, year, market);
        var revision = Revision(calendar, value);
        // F1：暴露 origin/confirmed/confidence/notes；老行按 imported/已确认兼容。
        return new JsonObject { ["market"] = market, ["year"] = year, ["configured"] = calendar is not null,
            ["source"] = calendar?.Source, ["holidays"] = new JsonArray(calendar?.Holidays.Select(day => (JsonNode?)JsonValue.Create(day)).ToArray() ?? []),
            ["revision"] = revision, ["origin"] = value is null ? calendar is null ? "missing" : "builtin" : calendar!.Origin,
            ["confirmed"] = calendar?.Confirmed ?? true, ["confidence"] = calendar?.Confidence, ["notes"] = calendar?.Notes };
    }

    public JsonObject Status(DateOnly today, string market = "CN")
    {
        var current = View(today.Year, market);
        var next = View(today.Year + 1, market);
        var warnings = new JsonArray();
        if (!current["configured"]!.GetValue<bool>()) warnings.Add($"{today.Year}年日历缺失，自动报告暂停，请导入交易所官方休市日期。");
        var prepare = today.Month >= 10;
        if (prepare && !next["configured"]!.GetValue<bool>()) warnings.Add($"请准备{today.Year + 1}年交易日历，官方公告发布后导入；未导入前跨年自动报告会暂停。");
        return new JsonObject { ["current"] = current, ["next"] = next, ["prepare_next_year"] = prepare, ["warnings"] = warnings };
    }

    public Calendar? Load(int year, string market = "CN")
    {
        market = MarketId(market);
        var calendar = database.Read(sql => Parse(sql.Scalar("SELECT value FROM site_settings WHERE key=$1", Key(market, year)) as string, year, market));
        // F1：未确认的自动日历仅在 QUANT_CALENDAR_AUTO_ACCEPT=true 时采纳。
        return Effective(calendar) ? calendar : null;
    }
    public bool? TradingDay(DateOnly day, string market = "CN")
    {
        var calendar = Load(day.Year, market);
        return calendar is null ? null : day.DayOfWeek is not (DayOfWeek.Saturday or DayOfWeek.Sunday) && !calendar.Holidays.Contains(day.ToString("yyyy-MM-dd"));
    }
    public async Task<bool?> TradingDayAsync(DateOnly day, CancellationToken cancellation = default, string market = "CN") =>
        (await SnapshotAsync(day.Year, cancellation)).TradingDay(MarketId(market), day);

    public async Task<MarketCalendars> SnapshotAsync(int year, CancellationToken cancellation = default)
    {
        if (year is < 2000 or > 2100) return MarketCalendars.Empty;
        await snapshotGate.WaitAsync(cancellation);
        try
        {
            if (cached is { } saved && saved.Year == year && saved.Expires > Now) return saved.Data;
            var identities = MarketCalendars.Markets.SelectMany(market => Enumerable.Range(year - 1, 3)
                .Where(value => value is >= 2000 and <= 2100).Select(value => (Market: market, Year: value))).ToArray();
            var rows = await database.QueryAsync("SELECT key,value FROM site_settings WHERE key=ANY($1)", [identities.Select(item => Key(item.Market, item.Year)).ToArray()], cancellation);
            var values = rows.OfType<JsonObject>().ToDictionary(row => row["key"]!.GetValue<string>(), row => row["value"]!.GetValue<string>());
            var entries = new List<CalendarYear>();
            foreach (var id in identities)
            {
                var raw = values.GetValueOrDefault(Key(id.Market, id.Year));
                // F1：快照仅收录 effective 日历（未确认自动日历看 autoAccept）。
                if (Parse(raw, id.Year, id.Market) is { } calendar && Effective(calendar))
                    entries.Add(new(id.Market, id.Year, calendar.Source, Revision(calendar, raw), calendar.Holidays.Select(DateOnly.Parse).ToFrozenSet()));
            }
            var result = new MarketCalendars(entries);
            Volatile.Write(ref cached, new(year, result, Now.AddSeconds(30)));
            return result;
        }
        finally { snapshotGate.Release(); }
    }
    private static string Revision(Calendar? calendar, string? raw) => calendar is null ? "missing" :
        Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(raw ?? JsonSerializer.Serialize(calendar, FinanceJson.Options)))).ToLowerInvariant();

    private static Calendar? Parse(string? raw, int year, string market)
    {
        if (raw is null)
        {
            var builtin = MarketCalendars.Builtin.Load(market, year);
            return builtin is null ? null : new(year, builtin.Source, builtin.Holidays.Order().Select(day => day.ToString("yyyy-MM-dd")).ToArray(), market);
        }
        try
        {
            var calendar = JsonSerializer.Deserialize<Calendar>(raw, FinanceJson.Options);
            return calendar is not null && calendar.Year == year && calendar.Market == market && calendar.Holidays is not null
                && calendar.Holidays.All(text => DateOnly.TryParseExact(text, "yyyy-MM-dd", out var day) && day.Year == year) ? calendar : null;
        }
        catch (JsonException) { return null; }
    }
}
