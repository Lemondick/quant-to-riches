using System.Text.Json.Nodes;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Api.Services;

public sealed class PublicSnapshotRefresher(ComputeService compute, ComputeJobRepository jobs, PublicMarketSnapshot snapshot, PublicSnapshotRepository repository,
    TradingCalendarRepository calendar, TimeProvider clock, IConfiguration configuration, IHostEnvironment environment, ILogger<PublicSnapshotRefresher> logger) : BackgroundService
{
    private int detailOffset;
    public static bool InMarketWindow(DateTimeOffset utc, bool? tradingDay)
    {
        var time = TimeOnly.FromDateTime(utc.ToOffset(TimeSpan.FromHours(8)).DateTime);
        return tradingDay == true && time >= new TimeOnly(9, 15) && time <= new TimeOnly(15, 5);
    }
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var first = true;
        while (!stoppingToken.IsCancellationRequested)
        {
            var nextRefresh = clock.GetUtcNow().AddSeconds(30);
            try
            {
                var saved = await repository.MarketAsync(stoppingToken);
                if (saved is not null) snapshot.Publish(saved.Data, saved.CompletedAt);
                var now = clock.GetUtcNow();
                var day = DateOnly.FromDateTime(now.ToOffset(TimeSpan.FromHours(8)).DateTime);
                var trading = await calendar.TradingDayAsync(day, stoppingToken);
                snapshot.SetTradingDay(day, trading);
                var enabled = configuration["QUANT_PUBLIC_REFRESH_ENABLED"] is "true" || configuration["QUANT_PUBLIC_REFRESH_ENABLED"] != "false" && environment.IsProduction();
                if (enabled && configuration["QUANT_WORKER_SECRET"] is { Length: >= 32 } && (first || InMarketWindow(now, trading)))
                    await Refresh(stoppingToken);
                first = false;
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception error) when (error is not OutOfMemoryException)
            {
                snapshot.Failed();
                logger.LogWarning("Public snapshot refresh failed ({ErrorType})", error.GetType().Name);
            }
            using var interval = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
            var remaining = nextRefresh - clock.GetUtcNow();
            interval.CancelAfter(remaining > TimeSpan.Zero ? remaining : TimeSpan.FromMilliseconds(1));
            try
            {
                await snapshot.WaitForRefresh(interval.Token);
                // Explicit refresh is allowed outside the automatic trading window.
                if (configuration["QUANT_WORKER_SECRET"] is { Length: >= 32 }) await Refresh(stoppingToken);
            }
            catch (OperationCanceledException) when (!stoppingToken.IsCancellationRequested) { }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception error) when (error is not OutOfMemoryException)
            {
                snapshot.Failed();
                logger.LogWarning("Explicit public snapshot refresh failed ({ErrorType})", error.GetType().Name);
            }
        }
    }
    private async Task Refresh(CancellationToken cancellation)
    {
        var input = await repository.WarmInputsAsync(cancellation);
        if (input.UserId is null) return;
        await repository.TrimAsync(cancellation);
        async Task Warm(string kind, object payload)
        {
            try
            {
                var result = await compute.Run(input.UserId, kind, payload, cancellation, reuseCompleted: false);
                if (kind == "market.snapshot" && result is JsonObject data)
                {
                    var saved = await repository.MarketAsync(cancellation);
                    if (saved is not null) snapshot.Publish(data, saved.CompletedAt);
                    else snapshot.Failed();
                }
            }
            catch (ApplicationFailure error)
            {
                if (kind == "market.snapshot") snapshot.Failed();
                logger.LogWarning("Public refresh {Kind} failed ({Code})", kind, error.Code);
            }
            catch (QuantAssistant.Core.Jobs.JobQuotaExceeded) { logger.LogDebug("Public refresh deferred while the account queue is full"); }
        }
        // Historical bars are independent and cheap; queue them before the slower all-market feed.
        foreach (var symbol in new[] { "000300", "000001", "399001", "399006" })
            await Queue("market.history", new { symbol });
        // Refresh the common market snapshot first so slower individual funds cannot delay it.
        await Warm("market.snapshot", new { section = (string?)null });
        async Task Queue(string kind, object payload)
        {
            try
            {
                var json = System.Text.Json.JsonSerializer.Serialize(payload);
                await jobs.EnqueueAsync(input.UserId, kind, json, clock.GetUtcNow().UtcDateTime.AddSeconds(55), cancellation: cancellation);
            }
            catch (QuantAssistant.Core.Jobs.JobQuotaExceeded) { logger.LogDebug("Public refresh deferred while the account queue is full"); }
        }
        // Only enqueue auxiliary refreshes here; they cannot hold up the next market refresh.
        await Queue("market.quotes", new { stocks = input.Stocks.Take(16).ToArray(), indices = new[] { "000001", "399001", "399006", "000300" } });
        await Queue("market.news", new { limit = 50 });
        foreach (var batch in input.Funds.Chunk(20)) await Queue("fund.valuations", new { symbols = batch });
        // Bound detail warm-up work per cycle and rotate through observed funds.
        foreach (var symbol in input.Funds.Skip(detailOffset).Concat(input.Funds.Take(detailOffset)).Take(3))
            await Queue("fund.detail", new { symbol });
        if (input.Funds.Length > 0) detailOffset = (detailOffset + 3) % input.Funds.Length;
    }
}
