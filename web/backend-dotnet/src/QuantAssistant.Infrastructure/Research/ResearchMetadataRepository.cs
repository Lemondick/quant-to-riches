using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Research;

/// <summary>E5：研究元数据仓储（原 ResearchMetadataController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class ResearchMetadataRepository(IAccountDatabase database)
{
    public static readonly string[] Indicators = ["interest_rate", "pmi", "social_financing"];
    private const string FactorColumns = "factor,as_of_date,ic,ir,sample_count,source,created_at";
    private const string MacroColumns = "indicator,observed_on,value,unit,source,created_at";
    private const string BenchmarkColumns = "benchmark_id,name,components_json AS components,is_default,created_at,updated_at";

    public async Task<JsonObject> RecordFactorAsync(string userId, string factor, DateOnly asOfDate, double? ic, double? ir,
        int sampleCount, string source, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var row = (await sql.QueryAsync($"""
                INSERT INTO factor_observations(user_id,factor,as_of_date,ic,ir,sample_count,source,created_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,now()) ON CONFLICT(user_id,factor,as_of_date) DO UPDATE SET
                ic=EXCLUDED.ic,ir=EXCLUDED.ir,sample_count=EXCLUDED.sample_count,source=EXCLUDED.source,created_at=EXCLUDED.created_at
                RETURNING {FactorColumns}
                """, userId, factor, asOfDate, ic, ir, sampleCount, source))[0]!.AsObject();
            var report = FactorReport(sql, userId, factor, 60);
            foreach (var alert in report["alerts"]!.AsArray().OfType<JsonObject>())
                await sql.ExecuteAsync("""
                    INSERT INTO factor_alerts(user_id,factor,alert_type,severity,message,observed_at,status,details_json)
                    VALUES($1,$2,$3,$4,$5,now(),'open',$6::json) ON CONFLICT(user_id,factor,alert_type,observed_at) DO NOTHING
                    """, userId, factor, alert["alert_type"]!.GetValue<string>(), alert["severity"]!.GetValue<string>(),
                    alert["message"]!.GetValue<string>(), alert.ToJsonString());
            return row;
        }, cancellation);

    public async Task<JsonArray> ListFactorAlertsAsync(string userId, string? factor, int limit, CancellationToken cancellation = default) =>
        await database.ReadAsync(sql =>
            sql.QueryAsync("SELECT id,factor,alert_type,severity,message,observed_at,status,details_json AS details FROM factor_alerts WHERE user_id=$1 AND ($2::text IS NULL OR factor=$2) ORDER BY observed_at DESC,id DESC LIMIT $3", userId, factor, limit), cancellation);

    public async Task<JsonObject> FactorReportAsync(string userId, string factor, int limit, CancellationToken cancellation = default) =>
        await database.ReadAsync(sql => Task.FromResult(FactorReport(sql, userId, factor, limit)), cancellation);

    public async Task<JsonArray> ListMacroObservationsAsync(string userId, string? indicator, int limit, CancellationToken cancellation = default) =>
        await database.ReadAsync(sql =>
            sql.QueryAsync($"SELECT {MacroColumns} FROM macro_observations WHERE user_id=$1 AND ($2::text IS NULL OR indicator=$2) ORDER BY observed_on DESC,id DESC LIMIT $3", userId, indicator, limit), cancellation);

    public async Task<JsonNode?> RecordMacroAsync(string userId, string indicator, DateOnly observedOn, double value,
        string unit, string source, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql => (await sql.QueryAsync($"""
            INSERT INTO macro_observations(user_id,indicator,observed_on,value,unit,source,created_at)
            VALUES($1,$2,$3,$4,$5,$6,now()) ON CONFLICT(user_id,indicator,observed_on,source) DO UPDATE SET
            value=EXCLUDED.value,unit=EXCLUDED.unit,created_at=EXCLUDED.created_at RETURNING {MacroColumns}
            """, userId, indicator, observedOn, value, unit, source))[0], cancellation);

    public async Task<JsonObject> MacroContextAsync(string userId, DateOnly target, CancellationToken cancellation = default) =>
        await database.ReadAsync(sql => Task.FromResult(MacroContext(sql, userId, target)), cancellation);

    public async Task<JsonArray> ListBenchmarksAsync(string userId, int limit, CancellationToken cancellation = default) =>
        await database.ReadAsync(sql =>
            sql.QueryAsync($"SELECT {BenchmarkColumns} FROM benchmark_configs WHERE user_id=$1 ORDER BY created_at,benchmark_id LIMIT $2", userId, limit), cancellation);

    public async Task<JsonNode?> CreateBenchmarkAsync(string userId, string id, string name, Dictionary<string, double> components,
        bool isDefault, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            if (isDefault) await sql.ExecuteAsync("UPDATE benchmark_configs SET is_default=false,updated_at=now() WHERE user_id=$1 AND is_default", userId);
            return (await sql.QueryAsync($"""
                INSERT INTO benchmark_configs(user_id,benchmark_id,name,components_json,is_default,created_at,updated_at)
                VALUES($1,$2,$3,$4::json,$5,now(),now()) ON CONFLICT(user_id,benchmark_id) DO UPDATE SET
                name=EXCLUDED.name,components_json=EXCLUDED.components_json,is_default=EXCLUDED.is_default,updated_at=EXCLUDED.updated_at
                RETURNING {BenchmarkColumns}
                """, userId, id, name, JsonSerializer.Serialize(components), isDefault))[0];
        }, cancellation);

    private static JsonObject FactorReport(IAccountSql sql, string userId, string factor, int limit)
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

    private static JsonObject MacroContext(IAccountSql sql, string userId, DateOnly target)
    {
        var rows = sql.Query($"SELECT {MacroColumns} FROM (SELECT DISTINCT ON(indicator) {MacroColumns},id FROM macro_observations WHERE user_id=$1 AND observed_on<=$2 ORDER BY indicator,observed_on DESC,id DESC) latest ORDER BY observed_on DESC,id DESC", userId, target);
        var missing = Indicators.Except(rows.OfType<JsonObject>().Select(r => r["indicator"]!.GetValue<string>())).Order().ToArray();
        int? age = rows.Count == 0 ? null : rows.OfType<JsonObject>().Max(r => target.DayNumber - DateOnly.Parse(r["observed_on"]!.GetValue<string>()).DayNumber);
        return new JsonObject
        {
            ["as_of_date"] = target.ToString("yyyy-MM-dd"), ["status"] = missing.Length == 0 && age <= 45 ? "valid" : rows.Count > 0 ? "stale" : "blocked",
            ["missing"] = new JsonArray(missing.Select(m => (JsonNode?)JsonValue.Create(m)).ToArray()),
            ["max_age_days"] = age, ["observations"] = rows, ["research_only"] = true,
        };
    }
}
