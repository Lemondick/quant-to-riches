using System.Threading.Channels;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Api.Services;

/// <summary>Bounded, batched operational counters, independent of financial commits.</summary>
public sealed class UsageCounterService(IAccountRepository accounts, ILogger<UsageCounterService> logger) : BackgroundService
{
    private readonly Channel<UsageDelta> pending = Channel.CreateBounded<UsageDelta>(4096);
    private long dropped;
    public long Dropped => Interlocked.Read(ref dropped);
    public void Observe(string user, string category, int status, long duration, DateTime now)
    {
        var day = DateOnly.FromDateTime(now.AddHours(8)).ToString("yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture);
        if (!pending.Writer.TryWrite(new(user, day, category, 1, status >= 400 ? 1 : 0, Math.Max(0, duration), now)))
        {
            if (Interlocked.Increment(ref dropped) == 1) logger.LogWarning("Usage counter buffer is full; financial commits are unaffected");
        }
    }
    private void Drain(Dictionary<(string User, string Day, string Category), UsageDelta> batch)
    {
        while (pending.Reader.TryRead(out var next))
        {
            var key = (next.UserId, next.Day, next.Category);
            batch[key] = batch.TryGetValue(key, out var old)
                ? next with { Requests = old.Requests + next.Requests, Errors = old.Errors + next.Errors,
                    DurationMs = old.DurationMs + next.DurationMs, LastActivityAt = old.LastActivityAt > next.LastActivityAt ? old.LastActivityAt : next.LastActivityAt }
                : next;
        }
    }
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var batch = new Dictionary<(string, string, string), UsageDelta>();
        try
        {
            while (await pending.Reader.WaitToReadAsync(stoppingToken))
            {
                await Task.Delay(50, stoppingToken);
                Drain(batch);
                while (batch.Count > 0)
                {
                    try { await accounts.RecordUsageBatchAsync(batch.Values.ToArray(), stoppingToken); batch.Clear(); }
                    catch (Exception error) when (error is not OperationCanceledException and not OutOfMemoryException)
                    {
                        logger.LogWarning("Usage batch deferred ({ErrorType})", error.GetType().Name);
                        await Task.Delay(1000, stoppingToken);
                    }
                }
            }
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
        finally
        {
            pending.Writer.TryComplete();
            Drain(batch);
            using var finish = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            try { await accounts.RecordUsageBatchAsync(batch.Values.ToArray(), finish.Token); }
            catch (Exception error) when (error is not OutOfMemoryException) { logger.LogWarning("Usage counters could not flush at shutdown ({ErrorType})", error.GetType().Name); }
        }
    }
}
