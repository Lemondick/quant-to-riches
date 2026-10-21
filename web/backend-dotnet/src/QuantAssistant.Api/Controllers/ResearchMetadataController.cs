using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/research")]
public sealed class ResearchMetadataController(AccountDatabase database) : ControllerBase
{
    public sealed record FactorBody(string Factor, DateOnly AsOfDate, double? Ic = null, double? Ir = null, int SampleCount = 0, string Source = "manual");
    public sealed record MacroBody(string Indicator, DateOnly ObservedOn, double Value, string Unit, string Source = "manual");
    public sealed record BenchmarkBody(string Name, Dictionary<string, double> Components, string? BenchmarkId = null, bool IsDefault = false);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static readonly string[] Indicators = ["interest_rate", "pmi", "social_financing"];
    private const string FactorColumns = "factor,as_of_date,ic,ir,sample_count,source,created_at";
    private const string MacroColumns = "indicator,observed_on,value,unit,source,created_at";
    private const string BenchmarkColumns = "benchmark_id,name,components_json AS components,is_default,created_at,updated_at";
    private static void Text(string value, int maximum)
    { if (string.IsNullOrWhiteSpace(value) || value.Length > maximum) throw new ArgumentException("文本参数无效"); }
    private static void Limit(int limit, int minimum = 1, int maximum = 500)
    { if (limit < minimum || limit > maximum) throw new ArgumentException("条数无效"); }

    [HttpPost("factors/observations")]
    public IActionResult RecordFactor([FromBody] FactorBody body)
    {
        Text(body.Factor, 80); Text(body.Source, 200);
        if (body.SampleCount < 0 || body.Ic is { } ic && (!double.IsFinite(ic) || ic is < -1 or > 1)
            || body.Ir is { } ir && !double.IsFinite(ir)) throw new ArgumentException("因子观测无效");
        return Ok(database.WriteNonLedger(UserId, sql =>
        {
            var row = sql.Query($"""
                INSERT INTO factor_observations(user_id,factor,as_of_date,ic,ir,sample_count,source,created_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,now()) ON CONFLICT(user_id,factor,as_of_date) DO UPDATE SET
                ic=EXCLUDED.ic,ir=EXCLUDED.ir,sample_count=EXCLUDED.sample_count,source=EXCLUDED.source,created_at=EXCLUDED.created_at
                RETURNING {FactorColumns}
                """, UserId, body.Factor.Trim(), body.AsOfDate, body.Ic, body.Ir, body.SampleCount, body.Source.Trim())[0]!.AsObject();
            var report = FactorReport(sql, UserId, body.Factor.Trim(), 60);
            foreach (var alert in report["alerts"]!.AsArray().OfType<JsonObject>())
                sql.Execute("""
                    INSERT INTO factor_alerts(user_id,factor,alert_type,severity,message,observed_at,status,details_json)
                    VALUES($1,$2,$3,$4,$5,now(),'open',$6::json) ON CONFLICT(user_id,factor,alert_type,observed_at) DO NOTHING
                    """, UserId, body.Factor.Trim(), alert["alert_type"]!.GetValue<string>(), alert["severity"]!.GetValue<string>(),
                    alert["message"]!.GetValue<string>(), alert.ToJsonString());
            return row;
        }));
    }

    [HttpGet("factors/alerts")]
    public IActionResult FactorAlerts([FromQuery] string? factor = null, [FromQuery] int limit = 100)
    {
        Limit(limit); if (factor is not null) Text(factor, 80);
        return Ok(new { items = database.Read(sql => sql.Query("SELECT id,factor,alert_type,severity,message,observed_at,status,details_json AS details FROM factor_alerts WHERE user_id=$1 AND ($2::text IS NULL OR factor=$2) ORDER BY observed_at DESC,id DESC LIMIT $3", UserId, factor, limit)) });
    }

    [HttpGet("factors/{factor}")]
    public IActionResult FactorMonitor(string factor, [FromQuery] int limit = 60)
    {
        Text(factor, 80); Limit(limit, 2, 730);
        return Ok(database.Read(sql => FactorReport(sql, UserId, factor.Trim(), limit)));
    }

    private static JsonObject FactorReport(AccountSql sql, string userId, string factor, int limit)
    {
        var rows = sql.Query($"SELECT {FactorColumns} FROM (SELECT {FactorColumns} FROM factor_observations WHERE user_id=$1 AND factor=$2 ORDER BY as_of_date DESC LIMIT $3) observations ORDER BY as_of_date", userId, factor, limit);
        var ics = rows.OfType<JsonObject>().Where(r => r["ic"] is not null).Select(r => r["ic"]!.GetValue<double>()).ToArray();
        var irs = rows.OfType<JsonObject>().Where(r => r["ir"] is not null).Select(r => r["ir"]!.GetValue<double>()).ToArray();
        var icSlope = Slope(ics); var irSlope = Slope(irs);
        var decay = icSlope <= -0.01 || irSlope <= -0.01;
        var alerts = new JsonArray();
        if (rows.Count == 0) alerts.Add(new JsonObject { ["alert_type"] = "missing", ["severity"] = "warning", ["message"] = $"因子 {factor} 尚无持续 IC/IR 观测" });
        else if (decay) alerts.Add(new JsonObject { ["alert_type"] = "decay", ["severity"] = "warning", ["message"] = $"因子 {factor} 的 IC/IR 出现衰减趋势", ["ic_slope"] = icSlope, ["ir_slope"] = irSlope });
        if (rows.Count > 0 && rows[^1]!["sample_count"]!.GetValue<int>() == 0) alerts.Add(new JsonObject { ["alert_type"] = "unstable", ["severity"] = "critical", ["message"] = $"因子 {factor} 最新观测没有有效样本" });
        return new JsonObject { ["factor"] = factor, ["observations"] = rows,
            ["mean_ic"] = ics.Length == 0 ? null : ics.Average(), ["mean_ir"] = irs.Length == 0 ? null : irs.Average(),
            ["ic_slope"] = icSlope, ["ir_slope"] = irSlope, ["decay"] = decay, ["alerts"] = alerts, ["research_only"] = true };
    }

    private static double? Slope(double[] values)
    {
        if (values.Length < 2) return null;
        var meanX = (values.Length - 1) / 2.0; var meanY = values.Average();
        var numerator = 0.0; var denominator = 0.0;
        for (var index = 0; index < values.Length; index++)
        { numerator += (index - meanX) * (values[index] - meanY); denominator += Math.Pow(index - meanX, 2); }
        return numerator / denominator;
    }

    [HttpGet("macro/observations")]
    public IActionResult MacroObservations([FromQuery] string? indicator = null, [FromQuery] int limit = 100)
    {
        Limit(limit); if (indicator is not null) Text(indicator, 60);
        return Ok(new { items = database.Read(sql => sql.Query($"SELECT {MacroColumns} FROM macro_observations WHERE user_id=$1 AND ($2::text IS NULL OR indicator=$2) ORDER BY observed_on DESC,id DESC LIMIT $3", UserId, indicator, limit)) });
    }

    [HttpPost("macro/observations")]
    public IActionResult RecordMacro([FromBody] MacroBody body)
    {
        if (!Indicators.Contains(body.Indicator) || !double.IsFinite(body.Value)) throw new ArgumentException("宏观观测无效");
        Text(body.Unit, 30); Text(body.Source, 200);
        return Ok(database.WriteNonLedger(UserId, sql => sql.Query($"""
            INSERT INTO macro_observations(user_id,indicator,observed_on,value,unit,source,created_at)
            VALUES($1,$2,$3,$4,$5,$6,now()) ON CONFLICT(user_id,indicator,observed_on,source) DO UPDATE SET
            value=EXCLUDED.value,unit=EXCLUDED.unit,created_at=EXCLUDED.created_at RETURNING {MacroColumns}
            """, UserId, body.Indicator, body.ObservedOn, body.Value, body.Unit.Trim(), body.Source.Trim())[0]));
    }

    [HttpGet("macro/context")]
    public IActionResult MacroContext([FromQuery(Name = "as_of")] DateOnly? asOf = null)
    {
        var target = asOf ?? DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        var rows = database.Read(sql => sql.Query($"SELECT {MacroColumns} FROM (SELECT DISTINCT ON(indicator) {MacroColumns},id FROM macro_observations WHERE user_id=$1 AND observed_on<=$2 ORDER BY indicator,observed_on DESC,id DESC) latest ORDER BY observed_on DESC,id DESC", UserId, target));
        var missing = Indicators.Except(rows.OfType<JsonObject>().Select(r => r["indicator"]!.GetValue<string>())).Order().ToArray();
        int? age = rows.Count == 0 ? null : rows.OfType<JsonObject>().Max(r => target.DayNumber - DateOnly.Parse(r["observed_on"]!.GetValue<string>()).DayNumber);
        return Ok(new { as_of_date = target, status = missing.Length == 0 && age <= 45 ? "valid" : rows.Count > 0 ? "stale" : "blocked", missing, max_age_days = age, observations = rows, research_only = true });
    }

    [HttpGet("benchmarks")]
    public IActionResult Benchmarks([FromQuery] int limit = 100)
    {
        Limit(limit);
        return Ok(new { items = database.Read(sql => sql.Query($"SELECT {BenchmarkColumns} FROM benchmark_configs WHERE user_id=$1 ORDER BY created_at,benchmark_id LIMIT $2", UserId, limit)) });
    }

    [HttpPost("benchmarks")]
    public IActionResult CreateBenchmark([FromBody] BenchmarkBody body)
    {
        Text(body.Name, 200); if (body.BenchmarkId is not null) Text(body.BenchmarkId, 128);
        if (body.Name.Any(char.IsControl) || body.BenchmarkId?.Any(char.IsControl) == true) throw new ArgumentException("基准名称或标识无效");
        if (body.Components is null || body.Components.Count is < 1 or > 20 || body.Components.Any(c => string.IsNullOrWhiteSpace(c.Key) || c.Key.Trim().Length > 80 || !double.IsFinite(c.Value) || c.Value <= 0)
            || Math.Abs(body.Components.Values.Sum() - 1) > 0.00000001) throw new ArgumentException("基准权重须为正且合计为1");
        var components = new Dictionary<string, double>(StringComparer.Ordinal);
        foreach (var (symbol, weight) in body.Components)
        {
            var code = symbol.Trim().ToUpperInvariant();
            if (code == "CASH.CNY") code = "CASH";
            if (code.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)) || !components.TryAdd(code, weight))
                throw new ArgumentException("基准代码不能含空白或重复，现金代码使用CASH");
        }
        var id = body.BenchmarkId?.Trim() ?? Guid.NewGuid().ToString("N");
        return Ok(database.WriteNonLedger(UserId, sql =>
        {
            if (body.IsDefault) sql.Execute("UPDATE benchmark_configs SET is_default=false,updated_at=now() WHERE user_id=$1 AND is_default", UserId);
            return sql.Query($"""
                INSERT INTO benchmark_configs(user_id,benchmark_id,name,components_json,is_default,created_at,updated_at)
                VALUES($1,$2,$3,$4::json,$5,now(),now()) ON CONFLICT(user_id,benchmark_id) DO UPDATE SET
                name=EXCLUDED.name,components_json=EXCLUDED.components_json,is_default=EXCLUDED.is_default,updated_at=EXCLUDED.updated_at
                RETURNING {BenchmarkColumns}
                """, UserId, id, body.Name.Trim(), JsonSerializer.Serialize(components), body.IsDefault)[0];
        }));
    }
}
