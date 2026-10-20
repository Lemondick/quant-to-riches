using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using QuantAssistant.Core.Market;

namespace QuantAssistant.Api.Services;

public sealed class PublicMarketSnapshot(TimeProvider clock, TradingCalendarRepository? calendars = null, IConfiguration? configuration = null)
{
    private readonly PublicRefreshSettings settings = PublicRefreshSettings.From(configuration);
    private readonly object gate = new();
    private PublicSnapshot? snapshot;
    private bool refreshFailed;
    private DateOnly calendarDay;
    private bool? tradingDay;
    private DateTimeOffset nextRequest;
    private bool refreshRequested;
    public void RequestRefresh()
    {
        lock (gate)
        {
            if (clock.GetUtcNow() < nextRequest) return;
            nextRequest = clock.GetUtcNow() + settings.Interval;
            refreshRequested = true;
        }
    }
    public void SetTradingDay(DateOnly day, bool? trading) { lock (gate) { calendarDay = day; tradingDay = trading; } }
    public static string SessionState(DateTimeOffset now, DateOnly day, bool? trading)
        => MarketSessions.Classify(now, day, trading);
    public bool TakeRefreshRequest()
    {
        lock (gate) { var requested = refreshRequested; refreshRequested = false; return requested; }
    }
    public bool Publish(JsonObject data, DateTime completedAt)
    {
        if (data["indices"] is not JsonArray indices || data["fund_rankings"] is not JsonArray funds || data["sector_rankings"] is not JsonArray sectors || indices.Count + funds.Count + sectors.Count == 0 || data["observed_at"] is not JsonValue || data["unavailable"] is not JsonArray { Count: < 3 } || data["stale"]?.GetValue<bool>() == true) { Failed(); return false; }
        lock (gate)
        {
            if (snapshot is not null && snapshot.CompletedAt >= completedAt) return false;
            snapshot = new(data.DeepClone().AsObject(), completedAt);
            refreshFailed = false;
            return true;
        }
    }
    public void Failed() { lock (gate) refreshFailed = true; }
    public static void UseLegacySectors(JsonObject data)
    {
        // Older tabs treat null percentages as zero and know only industry rows.
        // Keep that contract until a client explicitly requests the full catalog.
        var rows = (data["sector_rankings"]?.AsArray() ?? []).OfType<JsonObject>()
            .Where(row => (row["category"]?.GetValue<string>() ?? "industry") == "industry" && row["change_rate"] is JsonValue)
            .ToArray();
        var source = rows.FirstOrDefault()?["source"]?.GetValue<string>() ?? "unknown";
        data["sector_rankings"] = new JsonArray(rows.Where(row => (row["source"]?.GetValue<string>() ?? "unknown") == source)
            .OrderByDescending(row => decimal.TryParse(row["change_rate"]?.GetValue<string>(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var rate) ? rate : decimal.MinValue)
            .Select(row => row.DeepClone()).ToArray());
    }
    public PublicReadResult Read(string? section, bool forceRefresh = false)
    {
        if (section is not (null or "indices" or "funds" or "sectors")) throw new ArgumentException("行情分区无效");
        if (forceRefresh) RequestRefresh();
        lock (gate)
        {
            if (snapshot is null || clock.GetUtcNow().UtcDateTime - snapshot.CompletedAt > TimeSpan.FromDays(1))
            {
                RequestRefresh();
                throw new ApplicationFailure("public_snapshot_unavailable", 503, "行情快照正在准备，请稍后刷新。");
            }
            var data = snapshot.Data.DeepClone().AsObject();
            var stale = refreshFailed || clock.GetUtcNow().UtcDateTime - snapshot.CompletedAt > settings.Interval * 2;
            data["stale"] = stale;
            data["market_session"] = calendars is null ? SessionState(clock.GetUtcNow(), calendarDay, tradingDay) : MarketSessions.Classify(clock.GetUtcNow(), calendars.CurrentSnapshot);
            data["refresh_interval_seconds"] = (int)settings.Interval.TotalSeconds;
            PublicDataQuality.Apply("market.snapshot",data,clock.GetUtcNow(),calendars:calendars?.CurrentSnapshot);
            if (section is not null)
            {
                if (section != "indices") data["indices"] = new JsonArray();
                if (section != "funds") data["fund_rankings"] = new JsonArray();
                if (section != "sectors") data["sector_rankings"] = new JsonArray();
                data["unavailable"] = new JsonArray((data["unavailable"]?.AsArray() ?? []).Where(item => item?.GetValue<string>() == section).Select(item => item?.DeepClone()).ToArray());
            }
            return new(data, stale ? "stale" : forceRefresh ? "refreshing" : "hit");
        }
    }
}
