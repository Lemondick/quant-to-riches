using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>
/// batch3 流 A：研报/资讯仓储（research_items）。
/// 研报为公开信息：入库去重（UNIQUE url），查询按标的/类型过滤分页（不按用户隔离）。
/// </summary>
public sealed class ResearchItemRepository(AccountDatabase database)
{
    private const string Columns = "id,symbol,kind,title,summary,source,url,published_at,fetched_at";

    /// <summary>用户持仓中的基金代码（本地主账本 + 活跃子账户），用作默认抓取标的。</summary>
    public async Task<string[]> UserFundCodesAsync(string userId, CancellationToken cancellation = default) =>
        (await database.QueryAsync("""
            SELECT DISTINCT symbol FROM (
                SELECT symbol FROM positions WHERE user_id=$1 AND asset_type='fund' AND quantity>0
                UNION
                SELECT p.symbol FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id
                WHERE p.user_id=$1 AND a.status='active' AND p.asset_type='fund' AND p.quantity>0) s
            """, [userId], cancellation)).OfType<JsonObject>()
            .Select(r => r["symbol"]!.GetValue<string>()).Where(s => s.Length is > 0 and <= 32).Distinct().ToArray();

    /// <summary>批量落库（去重）：返回实际新增条数。</summary>
    public async Task<int> InsertBatchAsync(IEnumerable<JsonObject> items, CancellationToken cancellation = default)
    {
        var list = items.ToList();
        if (list.Count == 0) return 0;
        return await database.WriteSystemAsync(async sql =>
        {
            var inserted = 0;
            foreach (var item in list)
            {
                var kind = item["kind"]?.GetValue<string>();
                if (kind is not ("report" or "news")) kind = "report";
                var url = item["url"]?.GetValue<string>() ?? "";
                if (url.Length is 0 or > 2000 || (item["title"]?.GetValue<string>() ?? "").Length is 0 or > 500) continue;
                DateTimeOffset? publishedAt = null;
                if (item["published_at"]?.GetValue<string>() is { } raw && DateTimeOffset.TryParse(raw, out var parsed))
                    publishedAt = parsed;
                inserted += await sql.ExecuteAsync("""
                    INSERT INTO research_items(symbol,kind,title,summary,source,url,published_at)
                    VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(url) DO NOTHING
                    """, item["symbol"]?.GetValue<string>() ?? "", kind,
                    item["title"]!.GetValue<string>(), item["summary"]?.GetValue<string>() ?? "",
                    item["source"]?.GetValue<string>() ?? "", url, publishedAt?.UtcDateTime);
            }
            return inserted;
        }, cancellation);
    }

    public async Task<JsonObject> ListAsync(string? symbol, string? kind, int page, int limit,
        CancellationToken cancellation = default)
    {
        if (page is < 1 or > 100000 || limit is < 1 or > 100) throw new ArgumentException("分页参数无效");
        if (kind is not (null or "report" or "news")) throw new ArgumentException("kind 无效");
        if (symbol is { Length: 0 or > 32 }) throw new ArgumentException("symbol 无效");
        return await database.ReadAsync(async sql =>
        {
            var rows = await sql.QueryAsync($"""
                SELECT {Columns} FROM research_items
                WHERE ($1::text IS NULL OR $1='' OR symbol=$1) AND ($2::text IS NULL OR kind=$2)
                ORDER BY published_at DESC NULLS LAST, id DESC LIMIT $3 OFFSET $4
                """, symbol, kind, limit, (page - 1) * limit);
            var total = Convert.ToInt64(await sql.ScalarAsync($"""
                SELECT count(*) FROM research_items
                WHERE ($1::text IS NULL OR $1='' OR symbol=$1) AND ($2::text IS NULL OR kind=$2)
                """, symbol, kind));
            return new JsonObject { ["items"] = rows, ["total"] = total, ["page"] = page, ["limit"] = limit };
        }, cancellation);
    }
}
