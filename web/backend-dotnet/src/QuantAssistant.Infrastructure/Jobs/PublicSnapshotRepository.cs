using System.Text.Json.Nodes;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Jobs;

public sealed record PublicSnapshot(JsonObject Data, DateTime CompletedAt);

public sealed class PublicSnapshotRepository(AccountDatabase database)
{
    public async Task<Dictionary<string,JsonObject>> FundClassificationFactsAsync(CancellationToken cancellation=default)
    {
        var rows=await database.QueryAsync("""
            SELECT DISTINCT ON(input_json->>'symbol') input_json->>'symbol' AS symbol,
                result_json->'classification_facts' AS classification_facts
            FROM public_compute_snapshots WHERE kind='fund.detail' AND completed_at>now()-interval '7 days'
                AND result_json->>'symbol'=input_json->>'symbol'
            ORDER BY input_json->>'symbol',completed_at DESC LIMIT 50000
            """,[],cancellation);
        return rows.OfType<JsonObject>().Where(row=>row["classification_facts"] is JsonObject)
            .ToDictionary(row=>row["symbol"]!.GetValue<string>(),row=>row,StringComparer.Ordinal);
    }
    public static readonly TimeSpan MarketRetention=TimeSpan.FromDays(14);
    public async Task<JsonObject> OfficialNavAsync(string[] symbols, CancellationToken cancellation = default)
    {
        var rows = await database.QueryAsync("""
            WITH detail AS MATERIALIZED (
                SELECT DISTINCT ON(input_json->>'symbol') input_json->>'symbol' AS symbol,result_json
                FROM public_compute_snapshots WHERE kind='fund.detail' AND input_json->>'symbol'=ANY($1)
                ORDER BY input_json->>'symbol',completed_at DESC
            ), points AS (
                SELECT symbol,item,result_json->>'observed_at' AS observed_at,result_json->>'fund_type' AS fund_type,result_json->'nav_metadata' AS nav_metadata,
                    row_number() OVER(PARTITION BY symbol ORDER BY item->>'nav_date' DESC) AS rank
                FROM detail CROSS JOIN LATERAL jsonb_array_elements(result_json->'nav_history') item
                WHERE item->>'source' IN ('eastmoney','tencent','danjuan')
                    AND item->>'nav_date' ~ '^\d{4}-\d{2}-\d{2}$'
                    AND item->>'nav_date' <= to_char(now() AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')
            ) SELECT symbol,item,observed_at,fund_type,nav_metadata FROM points WHERE rank<=2 ORDER BY symbol,item->>'nav_date'
            """, [symbols], cancellation);
        var result = new JsonObject();
        foreach (var group in rows.OfType<JsonObject>().GroupBy(row => row["symbol"]!.GetValue<string>()))
        {
            var first = group.First(); var metadata = first["nav_metadata"] as JsonObject;
            result[group.Key] = new JsonObject { ["points"] = new JsonArray(group.Select(row => row["item"]!.DeepClone()).ToArray()),
                ["observed_at"] = first["observed_at"]?.DeepClone(), ["fetched_at"] = metadata?["fetched_at"]?.DeepClone() ?? first["observed_at"]?.DeepClone(),
                ["fund_type"] = first["fund_type"]?.DeepClone(), ["nav_freshness_policy"] = metadata?["nav_freshness_policy"]?.DeepClone(),
                ["nav_valuation_weekday"] = metadata?["nav_valuation_weekday"]?.DeepClone(), ["nav_publication_lag_sessions"] = metadata?["nav_publication_lag_sessions"]?.DeepClone() };
        }
        return result;
    }

    public Task<int> TrimAsync(CancellationToken cancellation = default) => database.ReadAsync(sql => sql.ExecuteAsync("DELETE FROM public_compute_snapshots WHERE (kind='market.snapshot' AND completed_at<now()-make_interval(secs=>$1)) OR (kind<>'market.snapshot' AND expires_at<now()-interval '7 days')",(int)MarketRetention.TotalSeconds), cancellation);
    public async Task<CachedPublicResult?> ReadAsync(string kind, string input, CancellationToken cancellation = default)
    {
        if (!ComputeJobRepository.IndependentPublicKinds.Contains(kind)) throw new ArgumentException("公开资料类型无效");
        var age = kind=="market.snapshot"?(int)MarketRetention.TotalSeconds:kind is "fund.detail" or "market.history" or "fund.catalog" ? 7 * 86400 : 86400;
        var rows = await database.QueryAsync("SELECT result_json,completed_at,expires_at>now() AS fresh FROM public_compute_snapshots WHERE kind=$1 AND input_json=$2::jsonb AND completed_at>now()-make_interval(secs=>$3) ORDER BY completed_at DESC LIMIT 1", [kind, input, age], cancellation);
        return rows.FirstOrDefault() is JsonObject row ? new CachedPublicResult(row["result_json"]!.ToJsonString(), row["fresh"]!.GetValue<bool>(), DateTimeOffset.Parse(row["completed_at"]!.GetValue<string>()).UtcDateTime) : null;
    }

    public async Task<PublicSnapshot?> MarketAsync(CancellationToken cancellation = default)
    {
        var rows = await database.QueryAsync("""
            SELECT result_json,completed_at FROM public_compute_snapshots
            WHERE kind='market.snapshot' AND input_json->>'section' IS NULL AND completed_at>now()-make_interval(secs=>$1)
            ORDER BY completed_at DESC LIMIT 1
            """, [(int)MarketRetention.TotalSeconds], cancellation);
        return rows.FirstOrDefault() is JsonObject row
            ? new PublicSnapshot(row["result_json"]!.AsObject(), DateTimeOffset.Parse(row["completed_at"]!.GetValue<string>()).UtcDateTime) : null;
    }

    public async Task<JsonObject> ValuationsAsync(string[] symbols, CancellationToken cancellation = default)
    {
        var rows = await database.QueryAsync("""
            SELECT DISTINCT ON (item->>'symbol') item,completed_at
            FROM public_compute_snapshots s CROSS JOIN LATERAL jsonb_array_elements(s.result_json->'items') item
            WHERE kind='fund.valuations' AND completed_at>now()-interval '60 seconds' AND item->>'symbol'=ANY($1)
            ORDER BY item->>'symbol',completed_at DESC
            """, [symbols], cancellation);
        var items = new JsonArray(rows.OfType<JsonObject>().Select(row => row["item"]!.DeepClone()).ToArray());
        var available = items.OfType<JsonObject>().Select(item => item["symbol"]!.GetValue<string>()).ToHashSet(StringComparer.Ordinal);
        return new JsonObject {
            ["items"] = items, ["unavailable"] = new JsonArray(symbols.Where(symbol => !available.Contains(symbol)).Select(symbol => (JsonNode?)JsonValue.Create(symbol)).ToArray()),
            ["estimate_only"] = items.All(item => item!["valuation_type"]?.GetValue<string>() == "intraday_estimate"),
            ["disclaimer"] = "盘中估值或最新官方净值仅供参考，最终以基金官方公告为准。"
        };
    }

    public async Task<JsonObject> QuotesAsync(string[] stocks, string[] indices, CancellationToken cancellation = default)
    {
        var rows = await database.QueryAsync("""
            SELECT DISTINCT ON (item->>'asset_type',item->>'symbol') item,completed_at
            FROM public_compute_snapshots s CROSS JOIN LATERAL jsonb_array_elements(s.result_json->'items') item
            WHERE kind='market.quotes' AND completed_at>now()-interval '60 seconds'
              AND ((item->>'asset_type'='stock' AND item->>'symbol'=ANY($1)) OR (item->>'asset_type'='index' AND item->>'symbol'=ANY($2)))
            ORDER BY item->>'asset_type',item->>'symbol',completed_at DESC
            """, [stocks, indices], cancellation);
        var items = new JsonArray(rows.OfType<JsonObject>().Select(row => row["item"]!.DeepClone()).ToArray());
        var available = items.OfType<JsonObject>().Select(item => (item["asset_type"]!.GetValue<string>(), item["symbol"]!.GetValue<string>())).ToHashSet();
        var missing = stocks.Where(symbol => !available.Contains(("stock", symbol))).Concat(indices.Where(symbol => !available.Contains(("index", symbol)))).Distinct();
        return new JsonObject {
            ["items"] = items, ["unavailable"] = new JsonArray(missing.Select(symbol => (JsonNode?)JsonValue.Create(symbol)).ToArray()),
            ["observed_at"] = items.OfType<JsonObject>().Select(item => item["observed_at"]?.GetValue<string>()).Max(),
            ["source"] = "public_snapshot", ["stale"] = items.Count == 0 || items.Any(item => item?["trade_time_verified"]?.GetValue<bool>() != true)
        };
    }

    public async Task<JsonObject?> NewsAsync(int limit, CancellationToken cancellation = default)
    {
        var rows = await database.QueryAsync("SELECT result_json,expires_at<now() AS stale FROM public_compute_snapshots WHERE kind='market.news' AND completed_at>now()-interval '24 hours' ORDER BY completed_at DESC LIMIT 1", [], cancellation);
        if (rows.FirstOrDefault() is not JsonObject row) return (JsonObject?)null;
        var data = row["result_json"]!.DeepClone().AsObject();
        data["items"] = new JsonArray(data["items"]!.AsArray().Take(limit).Select(item => item?.DeepClone()).ToArray());
        data["stale"] = row["stale"]!.DeepClone();
        return data;
    }

    public Task<(string? UserId, string[] Funds, string[] Stocks)> WarmInputsAsync(CancellationToken cancellation = default) => database.ReadAsync(async sql =>
    {
        var user = await sql.ScalarAsync("SELECT id FROM users WHERE status='active' ORDER BY (role='admin') DESC,created_at LIMIT 1") as string;
        var rows = await sql.QueryAsync("""
            SELECT DISTINCT p.symbol,p.asset_type FROM positions p JOIN users u ON u.id=p.user_id
            WHERE u.status='active' AND p.quantity>0 AND p.asset_type IN ('fund','stock')
            UNION
            SELECT item->>'symbol',item->>'asset_type' FROM user_documents d JOIN users u ON u.id=d.user_id
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(d.payload->'watchlist','[]'::jsonb)) item
            WHERE u.status='active' AND d.document_key='preferences' AND item->>'asset_type' IN ('fund','stock')
            ORDER BY 2,1 LIMIT 100
            """);
        string[] Select(string type) => rows.OfType<JsonObject>().Where(row => row["asset_type"]?.GetValue<string>() == type)
            .Select(row => row["symbol"]!.GetValue<string>()).Where(symbol => symbol.Length == 6 && symbol.All(char.IsAsciiDigit)).ToArray();
        return (user, Select("fund"), Select("stock"));
    }, cancellation);

    /// <summary>
    /// F3：公开源连续失败告警。B 在 market.snapshot 的 result_json 上报
    /// provider_failures:{provider:连续失败轮数}；达到阈值后给活跃管理员写 critical 告警
    ///（alert_key=public_source:&lt;provider&gt;），恢复后 resolve。B 未落地前无字段则静默。
    /// </summary>
    public async Task CheckPublicSourceFailuresAsync(JsonObject data, int alertRounds, CancellationToken cancellation = default)
    {
        if (data["provider_failures"] is not JsonObject failures) return;
        var failing = failures
            .Where(p => p.Value?.GetValue<int>() >= alertRounds)
            .Select(p => "public_source:" + p.Key)
            .ToArray();
        var admins = await database.QueryAsync("SELECT id FROM users WHERE status='active' AND role='admin'", [], cancellation);
        foreach (var admin in admins.OfType<JsonObject>())
        {
            var adminId = admin["id"]!.GetValue<string>();
            await database.WriteNonLedgerAsync(adminId, async sql =>
            {
                foreach (var (provider, node) in failures)
                {
                    var rounds = node?.GetValue<int>() ?? 0;
                    if (rounds < alertRounds) continue;
                    var key = "public_source:" + provider;
                    await sql.ExecuteAsync("INSERT INTO operational_alerts(user_id,alert_key,severity,message,status,first_seen_at,last_seen_at,details_json) VALUES($1,$2,'critical',$3,'open',now(),now(),$4::json) ON CONFLICT(user_id,alert_key,status) DO UPDATE SET severity=EXCLUDED.severity,message=EXCLUDED.message,last_seen_at=EXCLUDED.last_seen_at,details_json=EXCLUDED.details_json",
                        adminId, key, $"公开数据源 {provider} 已连续 {rounds} 轮失败，请检查数据源配置与网络。",
                        new JsonObject { ["provider"] = provider, ["failed_rounds"] = rounds }.ToJsonString());
                }
                // 不在失败集合中的 public_source 告警视为已恢复 → resolve。
                await sql.ExecuteAsync("UPDATE operational_alerts SET status='resolved',last_seen_at=now() WHERE user_id=$1 AND status='open' AND alert_key LIKE 'public_source:%' AND NOT (alert_key = ANY($2))",
                    adminId, failing);
                return 0;
            }, cancellation);
        }
    }
}

