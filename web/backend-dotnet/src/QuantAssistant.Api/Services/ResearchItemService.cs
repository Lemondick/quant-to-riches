using System.Text.Json.Nodes;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch3 流 A：研报/资讯抓取与入库编排。
/// POST api/v1/research/refresh：调 worker op research.fetch（batch3 跨流契约，复用 announcements.fetch 的抓取框架与去重逻辑），落库去重。
/// Scheduler 每日抓取（参考公告 CheckDailySync 接入模式：Tick 日期门控，逐用户执行，失败不中断 Tick）。
/// </summary>
public sealed class ResearchItemService(ResearchItemRepository repo, ComputeService compute, AccountDatabase database)
{
    private static readonly string[] DefaultKinds = ["report", "news"];

    public async Task<JsonObject> RefreshForUserAsync(string userId, string[]? symbols, string[]? kinds,
        CancellationToken cancellation)
    {
        var kindsList = (kinds ?? []).Select(k => k.Trim().ToLowerInvariant())
            .Where(k => k is "report" or "news").Distinct().ToArray();
        if (kindsList.Length == 0) kindsList = DefaultKinds;
        var symbolList = (symbols ?? []).Select(s => s.Trim()).Where(s => s.Length is > 0 and <= 32)
            .Distinct(StringComparer.Ordinal).ToArray();
        if (symbolList.Length == 0) symbolList = await repo.UserFundCodesAsync(userId, cancellation);
        var result = (await compute.Run(userId, "research.fetch",
            new JsonObject
            {
                ["symbols"] = new JsonArray(symbolList.Select(s => (JsonNode)JsonValue.Create(s)!).ToArray()),
                ["kinds"] = new JsonArray(kindsList.Select(k => (JsonNode)JsonValue.Create(k)!).ToArray()),
            }, cancellation, seconds: 300))?.AsObject();
        var items = result?["items"]?.AsArray()?.OfType<JsonObject>().ToList() ?? [];
        var inserted = await repo.InsertBatchAsync(items, cancellation);
        return new JsonObject
        {
            ["symbols"] = symbolList.Length, ["kinds"] = new JsonArray(kindsList.Select(k => (JsonNode)JsonValue.Create(k)!).ToArray()),
            ["fetched"] = items.Count, ["inserted"] = inserted, ["deduped"] = true,
        };
    }

    private DateOnly _lastSync = DateOnly.MinValue;

    /// <summary>Scheduler 每日抓取（Tick 10 秒一次，日期门控保证每天只跑一次）。</summary>
    public void CheckDailySync()
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        if (_lastSync == today) return;
        _lastSync = today;
        try
        {
            var users = database.Read(sql => sql.Query("SELECT id FROM users WHERE status='active' ORDER BY id"));
            foreach (var row in users.OfType<JsonObject>())
            {
                try
                {
                    RefreshForUserAsync(row["id"]!.GetValue<string>(), null, null, CancellationToken.None).GetAwaiter().GetResult();
                }
                catch (Exception) { /* 单个用户失败不中断其余 */ }
            }
        }
        catch (Exception) { /* 抓取失败不中断 Tick，次日重试 */ }
    }
}
