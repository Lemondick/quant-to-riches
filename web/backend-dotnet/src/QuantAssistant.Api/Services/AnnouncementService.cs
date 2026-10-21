using System.Text.Json.Nodes;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch2 任务8：公告抓取与入库编排。
/// POST api/v1/announcements/sync：按用户持仓基金调 worker op announcements.fetch（契约§2），落库去重。
/// Scheduler 每日抓取（参考 F1 rollover 接入模式：Tick 日期门控，逐用户执行，失败不中断 Tick）。
/// </summary>
public sealed class AnnouncementService(AnnouncementRepository repo, ComputeService compute, AccountDatabase database)
{
    public async Task<JsonObject> SyncForUserAsync(string userId, CancellationToken cancellation)
    {
        var codes = await repo.UserFundCodesAsync(userId, cancellation);
        if (codes.Length == 0) return new JsonObject { ["funds"] = 0, ["fetched"] = 0, ["inserted"] = 0 };
        var since = await repo.LatestPublishedAtAsync(cancellation)
            ?? DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)).AddDays(-30).ToString("yyyy-MM-dd");
        var result = (await compute.Run(userId, "announcements.fetch",
            new JsonObject { ["fund_codes"] = new JsonArray(codes.Select(c => (JsonNode)JsonValue.Create(c)!).ToArray()), ["since"] = since },
            cancellation, seconds: 300))?.AsObject();
        var items = result?["announcements"]?.AsArray()?.OfType<JsonObject>().ToList() ?? [];
        var inserted = await repo.InsertBatchAsync(items, cancellation);
        return new JsonObject { ["funds"] = codes.Length, ["fetched"] = items.Count, ["inserted"] = inserted, ["since"] = since };
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
                    SyncForUserAsync(row["id"]!.GetValue<string>(), CancellationToken.None).GetAwaiter().GetResult();
                }
                catch (Exception) { /* 单个用户失败不中断其余 */ }
            }
        }
        catch (Exception) { /* 抓取失败不中断 Tick，次日重试 */ }
    }
}
