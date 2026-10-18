using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Caching.Memory;
using System.Diagnostics;
using QuantAssistant.Core.Errors;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;
using Xunit.Abstractions;

namespace QuantAssistant.Api.Tests;

public sealed class PublicReadCacheTests(ITestOutputHelper output)
{
    private const string Input = "{\"symbol\":\"004243\"}";
    private const string Result = "{\"symbol\":\"004243\",\"stale\":false,\"observed_at\":\"2026-10-12T09:00:00Z\"}";
    private static (ComputeJobRepository Jobs, ComputeService Service, string JobId) Seed(PgTestUser user, IMemoryCache? cache = null)
    {
        var jobs = new ComputeJobRepository(user.Connection);
        var id = jobs.Enqueue(user.Id, "fund.detail", Input, DateTime.UtcNow.AddMinutes(2));
        var claim = jobs.Claim("cache-swr-" + Guid.NewGuid().ToString("N"), ["fund.detail"])!;
        Assert.True(jobs.Finish(user.Id, id, claim.Attempt, claim.OwnerToken, Result, null));
        var configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = new string('x', 40) }).Build();
        return (jobs, new(jobs, new AnalysisSnapshotRepository(new AccountDatabase(user.Connection)), configuration, cache ?? new MemoryCache(new MemoryCacheOptions())), id);
    }

    [PgFact]
    public async Task FreshPublicRead_DoesNotWaitForAWorkerOrEnqueueAnotherTask()
    {
        using var user = new PgTestUser();
        var (jobs, service, _) = Seed(user);
        var result = await service.ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
        Assert.Equal("hit", result.Cache);
        Assert.False(result.Data!["stale"]!.GetValue<bool>());
        Assert.Equal(1L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        Assert.True(jobs.ReadPublicCache(user.Id, "fund.detail", Input)!.Fresh);
    }

    [PgFact]
    public async Task OldPublicRead_ReturnsImmediatelyAndDeduplicatesBackgroundRefresh()
    {
        using var user = new PgTestUser();
        var (jobs, service, _) = Seed(user);
        user.Execute("UPDATE compute_jobs SET completed_at=now()-interval '1 hour',deadline=now()-interval '1 hour' WHERE user_id=$1", user.Id);
        for (var attempt = 0; attempt < 2; attempt++)
        {
            var result = await service.ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
                Assert.Equal("stale", result.Cache);
            Assert.True(result.Data!["stale"]!.GetValue<bool>());
            Assert.Equal("2026-10-12T09:00:00Z", result.Data["observed_at"]!.GetValue<string>());
        }
        Assert.Equal(2L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        Assert.False(jobs.ReadPublicCache(user.Id, "fund.detail", Input)!.Fresh);
    }

    [PgFact]
    public void PublicCache_RejectsOtherAccountsChangedInputsDisabledUsersAndExpiredEvidence()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        var (jobs, _, _) = Seed(user);
        Assert.Null(jobs.ReadPublicCache(other.Id, "fund.detail", Input));
        Assert.Null(jobs.ReadPublicCache(user.Id, "fund.detail", "{\"symbol\":\"000001\"}"));
        Assert.Throws<ArgumentException>(() => jobs.ReadPublicCache(user.Id, "analysis.profit", "{}"));
        user.Execute("UPDATE users SET status='disabled' WHERE id=$1", user.Id);
        Assert.Null(jobs.ReadPublicCache(user.Id, "fund.detail", Input));
        user.Execute("UPDATE users SET status='active' WHERE id=$1", user.Id);
        user.Execute("UPDATE compute_jobs SET completed_at=now()-interval '8 days' WHERE user_id=$1", user.Id);
        Assert.Null(jobs.ReadPublicCache(user.Id, "fund.detail", Input));
    }

    [PgFact]
    public async Task FullQueue_StillReturnsPublicEvidenceWithoutAddingAJob()
    {
        using var user = new PgTestUser();
        var (jobs, service, _) = Seed(user);
        user.Execute("UPDATE compute_jobs SET completed_at=now()-interval '1 hour' WHERE user_id=$1", user.Id);
        for (var i = 0; i < 20; i++) jobs.Enqueue(user.Id, "analysis.data_status", "{\"nonce\":" + i + "}", DateTime.UtcNow.AddMinutes(2));
        var result = await service.ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
        Assert.Equal("stale", result.Cache);
        Assert.Equal(21L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task MemoryHit_NeedsNoDatabaseAndDoesNotShareMutableNodes()
    {
        using var user = new PgTestUser();
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var (_, service, _) = Seed(user, cache);
        var first = await service.ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
        first.Data!["symbol"] = "changed-by-caller";
        // An invalid connection makes any accidental database access fail.
        var offline = Offline(cache);
        var watch = Stopwatch.StartNew();
        for (var i = 0; i < 100; i++)
        {
            var result = await offline.ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
            Assert.Equal("hit", result.Cache);
            Assert.Equal("004243", result.Data!["symbol"]!.GetValue<string>());
            result.Data["symbol"] = "changed-again";
        }
        watch.Stop();
        var average = watch.Elapsed.TotalMilliseconds / 100;
        output.WriteLine($"100 memory hits without a database: {average:F4} ms/request");
        Assert.True(average < 5, $"Cache hit average was {average:F4} ms");
        Assert.Equal(1L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }

    private static ComputeService Offline(IMemoryCache cache)
    {
        const string connection = "Host=127.0.0.1;Port=1;Database=unavailable;Username=unavailable;Timeout=1";
        return new(new ComputeJobRepository(connection), new AnalysisSnapshotRepository(new AccountDatabase(connection)),
            new ConfigurationBuilder().Build(), cache);
    }

    [PgFact]
    public async Task Run_CachesAllPublicKindsButNeverAccountSnapshotsOrOtherInputs()
    {
        using var user = new PgTestUser();
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var (jobs, service, _) = Seed(user, cache);
        foreach (var kind in ComputeJobRepository.IndependentPublicKinds)
        {
            if (kind != "fund.detail")
            {
                var id = jobs.Enqueue(user.Id, kind, Input, DateTime.UtcNow.AddMinutes(2));
                var claim = jobs.Claim("memory-" + Guid.NewGuid().ToString("N"), [kind])!;
                Assert.True(jobs.Finish(user.Id, id, claim.Attempt, claim.OwnerToken, Result, null));
            }
            await service.Run(user.Id, kind, new { symbol = "004243" }, CancellationToken.None);
            var result = await Offline(cache).Run(user.Id, kind, new { symbol = "004243" }, CancellationToken.None);
            Assert.Equal("004243", result!["symbol"]!.GetValue<string>());
        }
        foreach (var pending in new[]
        {
            Offline(cache).Run("other-account", "fund.detail", new { symbol = "004243" }, CancellationToken.None),
            Offline(cache).Run(user.Id, "fund.detail", new { symbol = "000001" }, CancellationToken.None),
            Offline(cache).Run(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None, accountSnapshot: true),
            Offline(cache).Run(user.Id, "analysis.decision", new { symbol = "004243" }, CancellationToken.None),
        }) await Assert.ThrowsAsync<ApplicationFailure>(() => pending);
        Assert.Equal((long)ComputeJobRepository.IndependentPublicKinds.Length, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task Refresh_BypassesMemoryAndProviderCacheWhilePreservingTheQueryHash()
    {
        using var user = new PgTestUser();
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var (jobs, service, _) = Seed(user, cache);
        await service.ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        using var queued = ComputeJobNotifications.Local.SubscribeQueue();
        var refresh = service.ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, timeout.Token, refresh: true);
        Assert.False(refresh.IsCompleted);
        await queued.WaitAsync(TimeSpan.FromSeconds(3), timeout.Token);
        var claim = jobs.Claim("refresh-" + Guid.NewGuid().ToString("N"), ["fund.detail"])!;
        Assert.True(System.Text.Json.Nodes.JsonNode.Parse(claim.InputJson)!["_refresh"]!.GetValue<bool>());
        const string updated = "{\"symbol\":\"004243\",\"stale\":false,\"observed_at\":\"2026-10-17T09:00:00Z\"}";
        Assert.True(jobs.Finish(user.Id, claim.JobId, claim.Attempt, claim.OwnerToken, updated, null));
        Assert.Equal("refresh", (await refresh).Cache);
        Assert.Contains("2026-10-17", jobs.ReadPublicCache(user.Id, "fund.detail", Input)!.ResultJson);
        var cached = await Offline(cache).ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
        Assert.Equal("2026-10-17T09:00:00Z", cached.Data!["observed_at"]!.GetValue<string>());
        Assert.Equal(2L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task StaleProviderEvidence_IsNeverStoredAsAFreshMemoryHit()
    {
        using var user = new PgTestUser();
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var (_, service, _) = Seed(user, cache);
        user.Execute("UPDATE compute_jobs SET result_json=jsonb_set(result_json,'{stale}','true') WHERE user_id=$1", user.Id);
        await service.Run(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
        await Assert.ThrowsAsync<ApplicationFailure>(() => Offline(cache).Run(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None));
    }

    [PgFact]
    public async Task MemoryExpiration_UsesOriginalCompletionTime()
    {
        using var user = new PgTestUser();
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var (_, service, _) = Seed(user, cache);
        user.Execute("UPDATE compute_jobs SET completed_at=now()-interval '299 seconds' WHERE user_id=$1", user.Id);
        await service.ReadPublic(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None);
        await Task.Delay(1200);
        await Assert.ThrowsAsync<ApplicationFailure>(() => Offline(cache).Run(user.Id, "fund.detail", new { symbol = "004243" }, CancellationToken.None));
    }
}
