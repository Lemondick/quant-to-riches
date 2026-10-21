using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Market;

namespace QuantAssistant.Api.Services;

public sealed class PublicSnapshotRefresher(ComputeService compute, ComputeJobRepository jobs, PublicMarketSnapshot snapshot, PublicSnapshotRepository repository,
    TradingCalendarRepository calendar, TimeProvider clock, IConfiguration configuration, IHostEnvironment environment, ILogger<PublicSnapshotRefresher> logger) : BackgroundService
{
    private int detailOffset, historyOffset;
    private readonly PublicRefreshSettings settings = PublicRefreshSettings.From(configuration);
    public static bool InMarketWindow(DateTimeOffset utc, MarketCalendars calendars, string? market=null)
        => market is not null?MarketSessions.InRefreshWindow(utc,calendars,market)
            :IndexRegistry.All.Where(row=>row.Enabled).Select(row=>row.Market).Distinct().Any(id=>MarketSessions.InRefreshWindow(utc,calendars,id));
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
                var calendars=await calendar.SnapshotAsync(day.Year,cycle.Token);
                var trading=calendars.TradingDay("CN",day);
                snapshot.SetTradingDay(day, trading);
                var enabled = configuration["QUANT_PUBLIC_REFRESH_ENABLED"] is "true" || configuration["QUANT_PUBLIC_REFRESH_ENABLED"] != "false" && environment.IsProduction();
                var requested = snapshot.TakeRefreshRequest();
                if (configuration["QUANT_WORKER_SECRET"] is { Length: >= 32 } && (requested || enabled && (first || InMarketWindow(now, calendars))))
                    await Refresh(expires, cycle.Token, first||requested);
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
    private async Task Refresh(DateTimeOffset expires, CancellationToken cancellation, bool primeAll)
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
            // F3：公开源连续失败达到阈值后给管理员写 critical 告警（B 未上报 provider_failures 则静默）。
            var alertRounds = int.TryParse(configuration["QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS"], out var rounds) && rounds > 0 ? rounds : 3;
            await repository.CheckPublicSourceFailuresAsync(saved.Data, alertRounds, task.Token);
        }
        async Task Queue(string kind, object payload, bool reuseCompleted=true)
        {
            var remaining = Budget();
            using var taskDeadline = new CancellationTokenSource(remaining, clock);
            using var task = CancellationTokenSource.CreateLinkedTokenSource(cancellation, taskDeadline.Token);
            try
            {
                var json = System.Text.Json.JsonSerializer.Serialize(payload);
                await jobs.EnqueueAsync(input.UserId, kind, json, DateTime.UtcNow.Add(remaining), reuseCompleted:reuseCompleted, cancellation: task.Token,pendingLimit:18);
            }
            catch (QuantAssistant.Core.Jobs.JobQuotaExceeded) { logger.LogDebug("Public refresh deferred while the account queue is full"); }
        }
        // Only enqueue auxiliary refreshes here; they cannot hold up the next market refresh.
        var now=clock.GetUtcNow();
        var connected=IndexRegistry.All.Where(row=>row.Enabled&&row.History?.VerificationStatus=="verified" && (primeAll||MarketSessions.InRefreshWindow(now,calendar.CurrentSnapshot,row.Market))).ToArray();
        foreach(var row in connected.Skip(historyOffset%Math.Max(1,connected.Length)).Concat(connected.Take(historyOffset%Math.Max(1,connected.Length))).Take(2))
            await Queue("market.history",row.LegacyHistorySymbol is { } legacy?new System.Text.Json.Nodes.JsonObject{["symbol"]=legacy}:new System.Text.Json.Nodes.JsonObject{["index_id"]=row.Id},reuseCompleted:false);
        if(connected.Length>0) historyOffset=(historyOffset+2)%connected.Length;
        // This legacy quotes route accepts CN codes; all four markets are already in the core snapshot.
        var quoteIndices=IndexRegistry.All.Where(row=>row.Enabled&&row.Market=="CN"&&row.Quote.VerificationStatus=="verified").Select(row=>row.ExchangeCode).ToArray();
        await Queue("market.quotes", new { stocks = input.Stocks.Take(Math.Max(0,20-quoteIndices.Length)).ToArray(), indices = quoteIndices });
        await Queue("market.news", new { limit = 50 });
        await Queue("fund.catalog", new { });
        foreach (var batch in input.Funds.Chunk(20)) await Queue("fund.valuations", new { symbols = batch });
        // Bound detail warm-up work per cycle and rotate through observed funds.
        foreach (var symbol in input.Funds.Skip(detailOffset).Concat(input.Funds.Take(detailOffset)).Take(3))
            await Queue("fund.detail", new { symbol });
        if (input.Funds.Length > 0) detailOffset = (detailOffset + 3) % input.Funds.Length;
    }
}
