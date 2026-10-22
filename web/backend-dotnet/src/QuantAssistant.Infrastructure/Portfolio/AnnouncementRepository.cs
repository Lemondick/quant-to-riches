using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>
/// batch2 任务8：基金公告仓储（fund_announcements）。
/// 公告为用户持仓基金的公开信息：入库去重（UNIQUE fund_code/title/source_url），
/// 公告流查询按基金/类型过滤分页。
/// </summary>
public sealed class AnnouncementRepository(AccountDatabase database)
{
    private const string Columns = "id,fund_code,title,kind,published_at,source_url,summary,created_at";

    /// <summary>用户持仓中的基金代码（本地主账本 + 活跃子账户）。</summary>
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
                if (kind is not ("dividend" or "manager_change" or "liquidation_risk" or "other")) kind = "other";
                DateTimeOffset? publishedAt = null;
                if (item["published_at"]?.GetValue<string>() is { } raw && DateTimeOffset.TryParse(raw, out var parsed))
                    publishedAt = parsed;
                inserted += await sql.ExecuteAsync("""
                    INSERT INTO fund_announcements(fund_code,title,kind,published_at,source_url,summary)
                    VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(fund_code,title,source_url) DO NOTHING
                    """, item["fund_code"]!.GetValue<string>(), item["title"]!.GetValue<string>(), kind,
                    publishedAt?.UtcDateTime, item["source_url"]?.GetValue<string>() ?? "", item["summary"]?.GetValue<string>() ?? "");
            }
            return inserted;
        }, cancellation);
    }

    public async Task<JsonObject> ListAsync(string userId, string? fundCode, string? kind, int page, int limit,
        CancellationToken cancellation = default)
    {
        if (page is < 1 or > 100000 || limit is < 1 or > 100) throw new ArgumentException("分页参数无效");
        if (kind is not (null or "dividend" or "manager_change" or "liquidation_risk" or "other")) throw new ArgumentException("kind 无效");
        // 仅返回用户持仓基金的公告（公告为公开信息，按用户持仓过滤展示）。
        // batch5-#7 结论：count(*) OVER() 合并曾试过——窗口函数迫使全量 9000 行物化后再 top-N，
        // 实测比两次独立查询更慢（9.4ms vs 7.2ms），故保留两次查询（均走 fund_announcements_code_time 索引）。
        var codes = await UserFundCodesAsync(userId, cancellation);
        return await database.ReadAsync(async sql =>
        {
            var rows = await sql.QueryAsync($"""
                SELECT {Columns} FROM fund_announcements
                WHERE fund_code = ANY($1) AND ($2::text IS NULL OR fund_code=$2) AND ($3::text IS NULL OR kind=$3)
                ORDER BY published_at DESC NULLS LAST, id DESC LIMIT $4 OFFSET $5
                """, codes, fundCode, kind, limit, (page - 1) * limit);
            var total = Convert.ToInt64(await sql.ScalarAsync("""
                SELECT count(*) FROM fund_announcements
                WHERE fund_code = ANY($1) AND ($2::text IS NULL OR fund_code=$2) AND ($3::text IS NULL OR kind=$3)
                """, codes, fundCode, kind));
            return new JsonObject { ["items"] = rows, ["total"] = total, ["page"] = page, ["limit"] = limit };
        }, cancellation);
    }

    /// <summary>最近一次抓取水位（用于 since 增量）。</summary>
    public async Task<string?> LatestPublishedAtAsync(CancellationToken cancellation = default) =>
        (await database.QueryAsync("SELECT max(published_at)::text AS latest FROM fund_announcements", [], cancellation))
            .FirstOrDefault()?["latest"]?.GetValue<string>();
}
