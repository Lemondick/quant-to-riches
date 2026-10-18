using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Services;

public sealed class DashboardSummaryService(AccountDatabase database, PublicMarketSnapshot market, TimeProvider clock, AccountDisplayCache? cache = null)
{
    // One statement provides a consistent MVCC view without repeated network round trips.
    private const string Query = """
        WITH owned_positions AS MATERIALIZED (
            SELECT p.*,coalesce(i.name,p.symbol) AS name FROM positions p
            LEFT JOIN instruments i ON i.user_id=p.user_id AND i.symbol=p.symbol WHERE p.user_id=$1
        ), prefs AS MATERIALIZED (
            SELECT coalesce((SELECT payload::jsonb FROM user_documents WHERE user_id=$1 AND document_key='preferences'),
                '{"watchlist":[],"read_news_ids":[],"revision":0,"updated_at":null}'::jsonb) AS payload
        ), watched AS MATERIALIZED (
            SELECT item->>'symbol' AS symbol,item->>'asset_type' AS asset_type
            FROM prefs CROSS JOIN LATERAL jsonb_array_elements(coalesce(payload->'watchlist','[]'::jsonb)) item
        ), funds AS MATERIALIZED (
            SELECT symbol FROM owned_positions WHERE asset_type='fund' AND quantity>0
            UNION SELECT symbol FROM watched WHERE asset_type='fund' ORDER BY symbol LIMIT 100
        ), valuations AS MATERIALIZED (
            SELECT DISTINCT ON(item->>'symbol') item FROM public_compute_snapshots s
            CROSS JOIN LATERAL jsonb_array_elements(s.result_json->'items') item
            WHERE s.kind='fund.valuations' AND s.completed_at>now()-interval '60 seconds'
                AND item->>'symbol' IN (SELECT symbol FROM funds)
            ORDER BY item->>'symbol',s.completed_at DESC
        ), quotes AS MATERIALIZED (
            SELECT DISTINCT ON(item->>'asset_type',item->>'symbol') item FROM public_compute_snapshots s
            CROSS JOIN LATERAL jsonb_array_elements(s.result_json->'items') item
            WHERE s.kind='market.quotes' AND s.completed_at>now()-interval '60 seconds'
                AND EXISTS(SELECT 1 FROM watched w WHERE w.symbol=item->>'symbol' AND w.asset_type=item->>'asset_type')
            ORDER BY item->>'asset_type',item->>'symbol',s.completed_at DESC
        )
        SELECT jsonb_build_object(
            'user_id',$1::text,'preferences',(SELECT payload FROM prefs),
            'portfolio',jsonb_build_object(
                'positions',coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'symbol',symbol,'asset_type',asset_type,'name',name,'quantity',quantity::text,
                    'average_cost',average_cost::text,'market_value',market_value::text,'as_of_date',as_of_date::text
                ) ORDER BY asset_type,symbol) FROM owned_positions),'[]'::jsonb),
                'total_value',(SELECT coalesce(sum(market_value),0)::text FROM owned_positions),
                'cash',(SELECT coalesce(sum(market_value),0)::text FROM owned_positions WHERE asset_type='cash'),
                'as_of_date',(SELECT max(as_of_date)::text FROM owned_positions)
            ),
            'valuations',jsonb_build_object(
                'items',coalesce((SELECT jsonb_agg(item ORDER BY item->>'symbol') FROM valuations),'[]'::jsonb),
                'unavailable',coalesce((SELECT jsonb_agg(symbol ORDER BY symbol) FROM funds WHERE symbol NOT IN(SELECT item->>'symbol' FROM valuations)),'[]'::jsonb),
                'estimate_only',coalesce((SELECT bool_and(item->>'valuation_type'='intraday_estimate') FROM valuations),true),
                'disclaimer','盘中估值或最新官方净值仅供参考，最终以基金官方公告为准。'
            ),
            'quotes',jsonb_build_object(
                'items',coalesce((SELECT jsonb_agg(item) FROM quotes),'[]'::jsonb),
                'unavailable',coalesce((SELECT jsonb_agg(symbol) FROM watched w WHERE asset_type IN('stock','index')
                    AND NOT EXISTS(SELECT 1 FROM quotes q WHERE q.item->>'symbol'=w.symbol AND q.item->>'asset_type'=w.asset_type)),'[]'::jsonb),
                'observed_at',(SELECT max(item->>'observed_at') FROM quotes),
                'source','public_snapshot','stale',coalesce((SELECT bool_or(item->>'trade_time_verified'<>'true') FROM quotes),true)
            )
        ) AS data
        """;

    public async Task<JsonObject> Read(string userId, CancellationToken cancellation, string? version = null)
    {
        async Task<JsonObject> Load(CancellationToken token) => (await database.QueryAsync(Query, [userId], token))[0]!["data"]!.AsObject();
        var result = cache is null ? await Load(cancellation) : await cache.Read(userId, "dashboard", version, Load, cancellation);
        var unavailable = new JsonArray();
        try { result["market"] = market.Read("indices").Data; }
        catch (ApplicationFailure error) when (error.Code == "public_snapshot_unavailable") { result["market"] = null; unavailable.Add("market.snapshot"); }
        if (result["valuations"]!["unavailable"]!.AsArray().Count > 0) unavailable.Add("fund.valuations");
        if (result["quotes"]!["unavailable"]!.AsArray().Count > 0) unavailable.Add("market.quotes");
        result["unavailable"] = unavailable;
        result["generated_at"] = clock.GetUtcNow().ToString("O");
        if(result["valuations"] is JsonObject valuations)PublicDataQuality.Apply("fund.valuations",valuations,clock.GetUtcNow());
        if(result["quotes"] is JsonObject quotes)PublicDataQuality.Apply("market.quotes",quotes,clock.GetUtcNow());
        return result;
    }
}
