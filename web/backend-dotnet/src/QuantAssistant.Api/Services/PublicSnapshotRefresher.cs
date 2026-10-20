using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Api.Services;

public sealed class PublicSnapshotRefresher(ComputeService compute, ComputeJobRepository jobs, PublicMarketSnapshot snapshot, PublicSnapshotRepository repository,
    TradingCalendarRepository calendar, TimeProvider clock, IConfiguration configuration, IHostEnvironment environment, ILogger<PublicSnapshotRefresher> logger) : BackgroundService
{
    private int detailOffset;
    private readonly PublicRefreshSettings settings = PublicRefreshSettings.From(configuration);
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
            var expires = clock.GetUtcNow() + settings.Deadline;
            using var deadline = new CancellationTokenSource(settings.Deadline, clock);
            using var cycle = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken, deadline.Token);
            try
            {
                var saved = await repository.MarketAsync(cycle.Token);
                if (saved is not null) snapshot.Publish(saved.Data, saved.CompletedAt);
                var now = clock.GetUtcNow();
                var day = DateOnly.FromDateTime(now.ToOffset(TimeSpan.FromHours(8)).DateTime);
                var trading = await calendar.TradingDayAsync(day, cycle.Token);
                snapshot.SetTradingDay(day, trading);
                var enabled = configuration["QUANT_PUBLIC_REFRESH_ENABLED"] is "true" || configuration["QUANT_PUBLIC_REFRESH_ENABLED"] != "false" && environment.IsProduction();
                var requested = snapshot.TakeRefreshRequest();
                if (configuration["QUANT_WORKER_SECRET"] is { Length: >= 32 } && (requested || enabled && (first || InMarketWindow(now, trading))))
                    await Refresh(expires, cycle.Token);
                first = false;
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception error) when (error is not OutOfMemoryException)
            {
                snapshot.Failed();
                logger.LogWarning("Public snapshot refresh failed ({ErrorType})", error.GetType().Name);
            }
            try
            {
                // Both automatic and coalesced explicit refreshes wait after completion.
                await Task.Delay(settings.Interval, clock, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
        }
    }
    private async Task Refresh(DateTimeOffset expires, CancellationToken cancellation)
    {
        var input = await repository.WarmInputsAsync(cancellation);
        if (input.UserId is null) return;
        await repository.TrimAsync(cancellation);
        TimeSpan Budget()
        {
            cancellation.ThrowIfCancellationRequested();
            var remaining = expires - clock.GetUtcNow();
            if (remaining < TimeSpan.FromSeconds(1)) throw new OperationCanceledException("Public refresh deadline elapsed", cancellation);
            return remaining < settings.TaskTimeout ? remaining : settings.TaskTimeout;
        }
        // Core market work must finish before any historical or fund auxiliary work.
        var budget = Budget();
        using (var taskDeadline = new CancellationTokenSource(budget, clock))
        using (var task = CancellationTokenSource.CreateLinkedTokenSource(cancellation, taskDeadline.Token))
        {
            await compute.Run(input.UserId, "market.snapshot", new { section = (string?)null }, task.Token,
                seconds: Math.Max(1, (int)budget.TotalSeconds), reuseCompleted: false);
            // Data and completion time must come from the same persisted result.
            var saved = await repository.MarketAsync(task.Token);
            if (saved is null || !snapshot.Publish(saved.Data, saved.CompletedAt))
                throw new ApplicationFailure("public_snapshot_unavailable", 503, "公开快照没有可发布的新观测。");
        }
        async Task Queue(string kind, object payload)
        {
            var remaining = Budget();
            using var taskDeadline = new CancellationTokenSource(remaining, clock);
            using var task = CancellationTokenSource.CreateLinkedTokenSource(cancellation, taskDeadline.Token);
            try
            {
                var json = System.Text.Json.JsonSerializer.Serialize(payload);
                await jobs.EnqueueAsync(input.UserId, kind, json, DateTime.UtcNow.Add(remaining), cancellation: task.Token);
            }
            catch (QuantAssistant.Core.Jobs.JobQuotaExceeded) { logger.LogDebug("Public refresh deferred while the account queue is full"); }
        }
        // Only enqueue auxiliary refreshes here; they cannot hold up the next market refresh.
        foreach (var symbol in new[] { "000300", "000001", "399001", "399006" })
            await Queue("market.history", new { symbol });
        await Queue("market.quotes", new { stocks = input.Stocks.Take(16).ToArray(), indices = new[] { "000001", "399001", "399006", "000300" } });
        await Queue("market.news", new { limit = 50 });
        foreach (var batch in input.Funds.Chunk(20)) await Queue("fund.valuations", new { symbols = batch });
        // Bound detail warm-up work per cycle and rotate through observed funds.
        foreach (var symbol in input.Funds.Skip(detailOffset).Concat(input.Funds.Take(detailOffset)).Take(3))
            await Queue("fund.detail", new { symbol });
        if (input.Funds.Length > 0) detailOffset = (detailOffset + 3) % input.Funds.Length;
    }
}
