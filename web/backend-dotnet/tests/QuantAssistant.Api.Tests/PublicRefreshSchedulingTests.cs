using System.Text.Json.Nodes;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Logging;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class PublicRefreshSchedulingTests
{
    private const string Market = """{"indices":[{"symbol":"000300","name":"Synthetic","asset_type":"index","price":"100","change_rate":"0","observed_at":"2026-10-19T10:00:00+08:00","source":"synthetic"}],"fund_rankings":[],"sector_rankings":[],"observed_at":"2026-10-19T10:00:00+08:00","unavailable":[],"stale":false}""";
    private sealed class EnvironmentStub : IHostEnvironment
    {
        public string EnvironmentName { get; set; } = Environments.Production;
        public string ApplicationName { get; set; } = "synthetic-refresher";
        public string ContentRootPath { get; set; } = AppContext.BaseDirectory;
        public IFileProvider ContentRootFileProvider { get; set; } = new NullFileProvider();
    }
    private static IConfiguration Configuration(params (string Key, string Value)[] extra) => new ConfigurationBuilder()
        .AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = "synthetic-public-refresh-secret-for-tests", ["QUANT_PUBLIC_REFRESH_ENABLED"] = "true" }
            .Concat(extra.Select(p => new KeyValuePair<string, string?>(p.Key, p.Value)))) .Build();
    private sealed class WarningLog : ILogger<PublicSnapshotRefresher>
    {
        internal int Warnings;
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(LogLevel level) => true;
        public void Log<TState>(LogLevel level, EventId id, TState state, Exception? error, Func<TState, Exception?, string> formatter)
        { if (level >= LogLevel.Warning) Interlocked.Increment(ref Warnings); }
    }
    private static PublicSnapshotRefresher Service(PgTestUser user, ManualClock clock, PublicMarketSnapshot snapshot, IMemoryCache memory, IConfiguration? config = null, ILogger<PublicSnapshotRefresher>? logger = null)
    {
        var database = new AccountDatabase(user.Connection);
        var jobs = new ComputeJobRepository(user.Connection);
        var repository = new PublicSnapshotRepository(database);
        var configuration = config ?? Configuration();
        return new(new ComputeService(jobs, new AnalysisSnapshotRepository(database), configuration, memory), jobs, snapshot, repository,
            new TradingCalendarRepository(database, clock), clock, configuration, new EnvironmentStub(), logger ?? NullLogger<PublicSnapshotRefresher>.Instance);
    }
    private static void OwnWarmup(PgTestUser user) => user.Execute("UPDATE users SET role='admin',created_at='1900-01-01' WHERE id=$1", user.Id);
    private static async Task Wait(Func<Task<bool>> condition)
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        while (!await condition()) await Task.Delay(20, deadline.Token);
    }
    private static Task Wait(Func<bool> condition) => Wait(() => Task.FromResult(condition()));
    private static async Task<ComputeJob> Claim(ComputeJobRepository jobs, string[] kinds)
    {
        ComputeJob? claimed = null;
        await Wait(async () => (claimed = await jobs.ClaimAsync("synthetic-public-refresh", kinds)) is not null);
        return claimed!;
    }
    private static async Task Stop(PublicSnapshotRefresher service)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        await service.StopAsync(timeout.Token);
    }
    private static string Result(PgTestUser user, string price = "100")
    {
        var data = JsonNode.Parse(Market)!.AsObject();
        data["refresh_test"] = user.Id;
        data["indices"]![0]!["price"] = price;
        return data.ToJsonString();
    }
    private static void CleanSnapshots(PgTestUser user) => user.Execute("DELETE FROM public_compute_snapshots WHERE result_json->>'refresh_test'=$1", user.Id);
    [Fact]
    public void EmptyResultCannotReplaceValidEvidenceOrItsSourceClock()
    {
        var clock = new ManualClock(DateTimeOffset.UtcNow);
        var snapshot = new PublicMarketSnapshot(clock);
        snapshot.Publish(JsonNode.Parse(Market)!.AsObject(), clock.GetUtcNow().UtcDateTime);
        var empty = JsonNode.Parse(Market)!.AsObject();
        empty["indices"] = new JsonArray();
        empty["observed_at"] = "2026-10-20T10:00:00+08:00";
        snapshot.Publish(empty, clock.GetUtcNow().UtcDateTime.AddSeconds(1));
        var read = snapshot.Read(null);
        Assert.Single(read.Data!["indices"]!.AsArray());
        Assert.Equal("2026-10-19T10:00:00+08:00", read.Data["observed_at"]!.GetValue<string>());
        Assert.Equal("stale", read.Cache);
    }
    [PgFact]
    public async Task ColdStartQueuesCoreMarketBeforeHistoricalBars()
    {
        using var user = new PgTestUser(); OwnWarmup(user);
        var clock = new ManualClock(DateTimeOffset.UtcNow);
        var snapshot = new PublicMarketSnapshot(clock);
        using var memory = new MemoryCache(new MemoryCacheOptions());
        using var service = Service(user, clock, snapshot, memory);
        await service.StartAsync(CancellationToken.None);
        try
        {
            var claimed = await Claim(new(user.Connection), ["market.snapshot", "market.history"]);
            Assert.Equal("market.snapshot", claimed.Kind);
        }
        finally { await Stop(service); }
    }
    [PgFact]
    public Task ThirtyFiveSecondRefreshWaitsAfterCompletion() => Slow(35);
    [PgFact]
    public Task SixtySecondRefreshWaitsAfterCompletion() => Slow(60);
    private static async Task Slow(int seconds)
    {
        using var user = new PgTestUser(); OwnWarmup(user);
        var clock = new ManualClock(DateTimeOffset.Parse("2026-10-19T02:00:00Z"));
        var snapshot = new PublicMarketSnapshot(clock);
        using var memory = new MemoryCache(new MemoryCacheOptions());
        using var service = Service(user, clock, snapshot, memory, Configuration(("QUANT_PUBLIC_REFRESH_TASK_TIMEOUT_SECONDS", "70")));
        var jobs = new ComputeJobRepository(user.Connection);
        await service.StartAsync(CancellationToken.None);
        try
        {
            var claimed = await Claim(jobs, ["market.snapshot"]);
            clock.Advance(TimeSpan.FromSeconds(seconds));
            Assert.True(await jobs.FinishAsync(claimed.UserId, claimed.JobId, claimed.Attempt, claimed.OwnerToken, Result(user), null));
            await Wait(() => (long)user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND kind='market.news'", user.Id)! > 0);
            await Task.Delay(200);
            Assert.Equal(1L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND kind='market.snapshot'", user.Id));
            clock.Advance(TimeSpan.FromSeconds(29));
            await Task.Delay(100);
            Assert.Equal(1L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND kind='market.snapshot'", user.Id));
            clock.Advance(TimeSpan.FromSeconds(1));
            await Wait(() => (long)user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND kind='market.snapshot'", user.Id)! == 2);
        }
        finally
        {
            await Stop(service);
            CleanSnapshots(user);
        }
    }
    [PgFact]
    public async Task TaskDeadlineCancelsWaitAndKeepsOldObservation()
    {
        using var user = new PgTestUser(); OwnWarmup(user);
        var clock = new ManualClock(DateTimeOffset.UtcNow);
        var snapshot = new PublicMarketSnapshot(clock);
        snapshot.Publish(JsonNode.Parse(Market)!.AsObject(), clock.GetUtcNow().UtcDateTime.AddSeconds(1));
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var log = new WarningLog();
        using var service = Service(user, clock, snapshot, memory, Configuration(("QUANT_PUBLIC_REFRESH_DEADLINE_SECONDS", "10"), ("QUANT_PUBLIC_REFRESH_TASK_TIMEOUT_SECONDS", "5")), log);
        await service.StartAsync(CancellationToken.None);
        try
        {
            var claimed = await Claim(new(user.Connection), ["market.snapshot"]);
            Assert.InRange((claimed.Deadline - DateTime.UtcNow).TotalSeconds, 0, 5.5);
            clock.Advance(TimeSpan.FromSeconds(6));
            await Wait(() => log.Warnings == 1);
            Assert.Equal("stale", snapshot.Read(null).Cache);
            Assert.Equal("2026-10-19T10:00:00+08:00", snapshot.Read(null).Data!["observed_at"]!.GetValue<string>());
            Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND kind<>'market.snapshot'", user.Id));
        }
        finally { await Stop(service); }
    }
    [PgFact]
    public async Task RepeatedFailuresAreRateBoundedAndRecoverWithoutAnotherLoop()
    {
        using var user = new PgTestUser(); OwnWarmup(user);
        var clock = new ManualClock(DateTimeOffset.Parse("2026-10-19T02:00:00Z"));
        var snapshot = new PublicMarketSnapshot(clock);
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var log = new WarningLog();
        using var service = Service(user, clock, snapshot, memory, logger: log);
        var jobs = new ComputeJobRepository(user.Connection);
        await service.StartAsync(CancellationToken.None);
        try
        {
            for (var attempt = 1; attempt <= 3; attempt++)
            {
                var claimed = await Claim(jobs, ["market.snapshot"]);
                Assert.True(await jobs.FinishAsync(claimed.UserId, claimed.JobId, claimed.Attempt, claimed.OwnerToken, null, "synthetic_unavailable"));
                await Wait(() => log.Warnings == attempt);
                await Task.Delay(50);
                clock.Advance(TimeSpan.FromSeconds(29));
                await Task.Delay(100);
                Assert.Equal((long)attempt, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND kind='market.snapshot'", user.Id));
                clock.Advance(TimeSpan.FromSeconds(1));
            }
            var recovery = await Claim(jobs, ["market.snapshot"]);
            Assert.True(await jobs.FinishAsync(recovery.UserId, recovery.JobId, recovery.Attempt, recovery.OwnerToken, Result(user, "101"), null));
            await Wait(() => { try { return snapshot.Read(null).Data!["indices"]![0]!["price"]!.GetValue<string>() == "101"; } catch (QuantAssistant.Core.Errors.ApplicationFailure) { return false; } });
            Assert.Equal(3, log.Warnings);
        }
        finally { await Stop(service); CleanSnapshots(user); }
    }
}
