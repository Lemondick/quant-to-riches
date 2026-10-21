using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch5 任务2：净资产总览。
/// 口径（文档注明）：
/// - 总资产 = 账本持仓市值合计（ILedgerRepository.ListPositionsAsync），与 /api/v1/portfolio 的 total_value 同口径；
/// - 历史曲线 = portfolio_valuations 的 (as_of_date, total_value)，?history_days= 限制天数（默认 90）；
/// - 负债：系统暂无负债登记表/接口，负债固定返回 0 并在 liabilities_note 注明；净资产 = 总资产 - 负债。
/// </summary>
public sealed class NetWorthService(ILedgerRepository ledger, AccountDatabase database)
{
    public async Task<JsonObject> SummaryAsync(string userId, int historyDays, CancellationToken cancellation = default)
    {
        var positions = await ledger.ListPositionsAsync(userId, cancellation);
        var total = positions.Sum(p => p.MarketValue);
        var asOf = positions.Count == 0 ? (DateOnly?)null : positions.Max(p => p.AsOfDate);
        var history = await database.ReadAsync(async sql =>
            await sql.QueryAsync("""
                SELECT as_of_date::text AS date, total_value::text AS total_value
                FROM portfolio_valuations
                WHERE user_id=$1 AND as_of_date >= CURRENT_DATE - make_interval(days => $2)
                ORDER BY as_of_date
                """, userId, Math.Clamp(historyDays, 1, 3650)), cancellation);
        return new JsonObject
        {
            ["total_assets"] = total.ToString(CultureInfo.InvariantCulture),
            ["liabilities"] = "0",
            ["net_worth"] = total.ToString(CultureInfo.InvariantCulture),
            ["liabilities_note"] = "系统暂无负债登记，负债按 0 计；净资产=总资产-负债。",
            ["as_of_date"] = asOf?.ToString("yyyy-MM-dd"),
            ["history"] = history,
        };
    }

    public async Task<JsonObject> AllocationAsync(string userId, CancellationToken cancellation = default)
    {
        var positions = (await ledger.ListPositionsAsync(userId, cancellation))
            .Where(p => p.MarketValue > 0).ToArray();
        var total = positions.Sum(p => p.MarketValue);
        var items = new JsonArray();
        foreach (var group in positions.GroupBy(p => BucketName(p.AssetType)).OrderBy(g => g.Key))
        {
            var value = group.Sum(p => p.MarketValue);
            items.Add(new JsonObject
            {
                ["name"] = group.Key,
                ["value"] = value.ToString(CultureInfo.InvariantCulture),
                ["weight"] = total > 0 ? Math.Round((double)value / (double)total, 4) : 0,
            });
        }
        return new JsonObject
        {
            ["total"] = total.ToString(CultureInfo.InvariantCulture),
            ["items"] = items,
            ["caliber"] = "与 /api/v1/portfolio 同口径（持仓市值合计）；仅计入市值>0 的持仓。",
        };
    }

    private static string BucketName(AssetType type) => type switch
    {
        AssetType.Stock => "股票",
        AssetType.Fund => "基金",
        AssetType.Index => "指数",
        AssetType.Cash => "现金",
        _ => "其他",
    };
}
