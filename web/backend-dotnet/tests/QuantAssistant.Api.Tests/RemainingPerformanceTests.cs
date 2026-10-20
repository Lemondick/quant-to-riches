using System.Diagnostics;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;
using Xunit.Abstractions;

namespace QuantAssistant.Api.Tests;

public sealed class RemainingPerformanceTests(ITestOutputHelper output)
{
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now = DateTimeOffset.Parse("2026-10-16T02:00:00Z");
        public override DateTimeOffset GetUtcNow() => Now;
    }
    private const string Market = """
        {"indices":[{"symbol":"000300","name":"沪深300","asset_type":"index","price":"100.25","change_rate":"1.25","observed_at":"2026-10-16T10:00:00+08:00","source":"synthetic"}],"fund_rankings":[],"sector_rankings":[],"observed_at":"2026-10-16T10:00:00+08:00","source":"synthetic","stale":false,"unavailable":[]}
        """;

    [Fact]
    public async Task AccountDisplayCache_CoalescesClonesAndSeparatesCommittedVersions()
    {
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var cache = new AccountDisplayCache(memory);
        var gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var calls = 0;
        async Task<JsonObject> Read(CancellationToken cancellation) { Interlocked.Increment(ref calls); await gate.Task.WaitAsync(cancellation); return new() { ["amount"] = "1.25" }; }
        var pending = Enumerable.Range(0, 50).Select(_ => cache.Read("user", "summary", "1:1", Read, CancellationToken.None)).ToArray();
        gate.SetResult();
        var results = await Task.WhenAll(pending);
        Assert.Equal(1, calls);
        results[0]["amount"] = "999";
        Assert.Equal("1.25", (await cache.Read("user", "summary", "1:1", Read, CancellationToken.None))["amount"]!.GetValue<string>());
        await cache.Read("user", "summary", "2:1", Read, CancellationToken.None);
        await cache.Read("other", "summary", "2:1", Read, CancellationToken.None);
        Assert.Equal(3, calls);
    }

    [PgFact]
    public async Task AuthenticatedDisplayCache_ImmediatelySeesCommittedHoldingsAndRejectsOldSessionScopes()
    {
        using var user = new PgTestUser(); user.SeedLedger();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient(); PgTestUser.AttachSession(client, user.Connection, user.Id);
        var before = await client.GetFromJsonAsync<JsonObject>("/api/v1/dashboard/summary");
        Assert.Equal(10000m, decimal.Parse(before!["portfolio"]!["cash"]!.GetValue<string>()));
        user.Execute("UPDATE positions SET quantity=123,market_value=123 WHERE user_id=$1 AND symbol='CNY'", user.Id);
        var after = await client.GetFromJsonAsync<JsonObject>("/api/v1/dashboard/summary");
        var portfolio = await client.GetFromJsonAsync<JsonObject>("/api/v1/portfolio");
        Assert.Equal(123m, decimal.Parse(after!["portfolio"]!["cash"]!.GetValue<string>()));
        Assert.Equal(123m, decimal.Parse(portfolio!["cash"]!.GetValue<string>()));
        client.DefaultRequestHeaders.Add("X-Session-Scope", "a-different-session");
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/v1/dashboard/summary")).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/api/v1/preferences/news/read", new { ids = new[] { "synthetic" } })).StatusCode);
    }

    [Fact]
    public async Task NotificationDelivery_IsMeasuredSeparatelyFromDurableCompletion()
    {
        var events = new ComputeJobNotifications();
        using var subscription = events.Subscribe("synthetic", "delivery");
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        var samples = new List<double>();
        for (var attempt = 0; attempt < 100; attempt++)
        {
            var waiting = subscription.WaitAsync(TimeSpan.FromSeconds(2), timeout.Token);
            events.Publish("synthetic", "delivery");
            samples.Add((await waiting)!.Value.TotalMilliseconds);
        }
        samples.Sort();
        output.WriteLine($"100 notification wake-ups: median={samples[49]:F3}ms p95={samples[94]:F3}ms max={samples[99]:F3}ms; boundary starts at signal publication");
        Assert.True(samples[94] < 5, $"Notification P95 was {samples[94]:F3}ms");
    }

    [PgFact]
    public async Task PublicOfficialNav_UsesOnlyPersistedAnonymousSourceObservations()
    {
        using var user = new PgTestUser();
        var symbol = "222" + Random.Shared.Next(100, 999);
        var day = DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(8)).Date;
        var input = new JsonObject { ["symbol"] = symbol };
        var result = new JsonObject { ["symbol"] = symbol, ["observed_at"] = DateTimeOffset.UtcNow.ToString("O"), ["nav_history"] = new JsonArray(
            new JsonObject { ["nav_date"] = day.AddDays(-1).ToString("yyyy-MM-dd"), ["unit_nav"] = "1.20", ["source"] = "eastmoney" },
            new JsonObject { ["nav_date"] = day.ToString("yyyy-MM-dd"), ["unit_nav"] = "1.25", ["source"] = "eastmoney" },
            new JsonObject { ["nav_date"] = day.ToString("yyyy-MM-dd"), ["unit_nav"] = "999.99", ["source"] = "synthetic_untrusted" }) };
        try
        {
            user.Execute("INSERT INTO public_compute_snapshots(kind,input_hash,input_json,result_json,completed_at,expires_at) VALUES('fund.detail',$1,$2::jsonb,$3::jsonb,now(),now()+interval '5 minutes')", symbol, input.ToJsonString(), result.ToJsonString());
            var read = await new PublicSnapshotRepository(new AccountDatabase(user.Connection)).OfficialNavAsync([symbol]);
            var points = read[symbol]!["points"]!.AsArray();
            Assert.Equal(2, points.Count);
            Assert.Equal("1.25", points[1]!["unit_nav"]!.GetValue<string>());
            Assert.All(points, point => Assert.Equal("eastmoney", point!["source"]!.GetValue<string>()));
        }
        finally { user.Execute("DELETE FROM public_compute_snapshots WHERE input_hash=$1", symbol); }
    }

    [Fact]
    public void ColdMarketSnapshot_FailsClosed_AndReadsNeedNoDatabaseOrWorker()
    {
        var clock = new Clock();
        var store = new PublicMarketSnapshot(clock);
        Assert.Equal("public_snapshot_unavailable", Assert.Throws<ApplicationFailure>(() => store.Read(null)).Code);
        store.Publish(JsonNode.Parse(Market)!.AsObject(), clock.Now.UtcDateTime);
        var read = store.Read(null);
        read.Data!["indices"]!.AsArray().Clear();
        Assert.Single(store.Read(null).Data!["indices"]!.AsArray());
        clock.Now = clock.Now.AddSeconds(61);
        var stale = store.Read(null);
        Assert.Equal("stale", stale.Cache);
        Assert.Equal("2026-10-16T10:00:00+08:00", stale.Data!["observed_at"]!.GetValue<string>());
        Assert.Empty(store.Read("funds").Data!["indices"]!.AsArray());
    }

    [Fact]
    public void FailedOrMalformedSnapshot_CannotReplaceVerifiedEvidence()
    {
        var clock = new Clock();
        var store = new PublicMarketSnapshot(clock);
        store.Publish(JsonNode.Parse(Market)!.AsObject(), clock.Now.UtcDateTime);
        store.Publish(new JsonObject(), clock.Now.AddSeconds(1).UtcDateTime);
        // Restoring the same persisted observation must not erase a refresh failure.
        store.Publish(JsonNode.Parse(Market)!.AsObject(), clock.Now.UtcDateTime);
        Assert.Equal("stale", store.Read(null).Cache);
        Assert.Single(store.Read(null).Data!["indices"]!.AsArray());
    }

    [Theory]
    [InlineData("2026-10-16T01:14:59Z", true, false)]
    [InlineData("2026-10-16T01:15:00Z", true, true)]
    [InlineData("2026-10-16T07:05:00Z", true, true)]
    [InlineData("2026-10-16T07:05:01Z", true, false)]
    [InlineData("2026-10-17T02:00:00Z", false, false)]
    [InlineData("2027-01-04T02:00:00Z", null, false)]
    public void Warmup_UsesShanghaiTradingWindowAndFailsClosedForMissingCalendar(string time, bool? trading, bool expected) =>
        Assert.Equal(expected, PublicSnapshotRefresher.InMarketWindow(DateTimeOffset.Parse(time), trading));

    [Fact]
    public async Task Notifications_BufferEarlySignals_AndCancelWaitsWithoutLeakingReaders()
    {
        var events = new ComputeJobNotifications();
        using var subscription = events.Subscribe("user", "job");
        events.Publish("user", "job");
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(1));
        await subscription.WaitAsync(TimeSpan.FromSeconds(5), timeout.Token);
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => subscription.WaitAsync(TimeSpan.FromSeconds(5), cancelled.Token));
    }

    [PgFact]
    public async Task Completion_WakesGatewayWithoutTwoHundredMillisecondPolling()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = new string('x', 40) }).Build();
        var service = new ComputeService(jobs, new AnalysisSnapshotRepository(new AccountDatabase(user.Connection)), configuration, cache);
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        using var queued = ComputeJobNotifications.Local.SubscribeQueue();
        var pending = service.Run(user.Id, "market.news", new { limit = 3 }, timeout.Token);
        await queued.WaitAsync(TimeSpan.FromSeconds(2), timeout.Token);
        var claim = await jobs.ClaimAsync("perf-" + user.Id, ["market.news"], timeout.Token);
        Assert.NotNull(claim);
        var watch = Stopwatch.StartNew();
        Assert.True(await jobs.FinishAsync(user.Id, claim.JobId, claim.Attempt, claim.OwnerToken, "{\"items\":[],\"unavailable\":false}", null, timeout.Token));
        await pending;
        output.WriteLine($"Finish and gateway wake: {watch.Elapsed.TotalMilliseconds:F3}ms");
        Assert.True(watch.Elapsed < TimeSpan.FromMilliseconds(150));
    }

    [PgFact]
    public async Task PostgresNotification_FromAnotherConnection_ReachesLocalWaiter()
    {
        using var user = new PgTestUser();
        using var listener = new ComputeNotificationListener(user.Connection, NullLogger<ComputeNotificationListener>.Instance);
        await listener.StartAsync(CancellationToken.None);
        try
        {
            await listener.Ready.WaitAsync(TimeSpan.FromSeconds(3));
            var jobs = new ComputeJobRepository(user.Connection);
            var id = await jobs.EnqueueAsync(user.Id, "fund.detail", "{\"symbol\":\"001111\"}", DateTime.UtcNow.AddSeconds(30));
            using var signal = ComputeJobNotifications.Local.Subscribe(user.Id, id);
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            var pending = signal.WaitAsync(TimeSpan.FromSeconds(5), timeout.Token);
            // Bypass the repository's local signal: only PostgreSQL NOTIFY can wake this waiter.
            user.Execute("UPDATE compute_jobs SET status='completed',result_json='{}',completed_at=now() WHERE user_id=$1 AND job_id=$2", user.Id, id);
            await pending;
        }
        finally { await listener.StopAsync(CancellationToken.None); }
    }

    [PgFact]
    public async Task AsyncDatabase_CancelsIoAndRollsBackFailedWrites()
    {
        using var user = new PgTestUser();
        var database = new AccountDatabase(user.Connection);
        using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(100));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => database.ReadAsync(async sql => { await sql.ExecuteAsync("SELECT pg_sleep(5)"); return 0; }, timeout.Token));
        await Assert.ThrowsAsync<InvalidOperationException>(() => database.WriteAsync<int>(user.Id, async sql =>
        {
            await sql.ExecuteAsync("INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,'perf-rollback',0,'{}')", user.Id);
            throw new InvalidOperationException("synthetic rollback");
        }));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM user_documents WHERE user_id=$1 AND document_key='perf-rollback'", user.Id));
    }

    [PgFact]
    public async Task FiftyConcurrentAsyncReads_CompleteWithoutBlockingTheThreadPool()
    {
        using var user = new PgTestUser();
        var database = new AccountDatabase(user.Connection);
        var watch = Stopwatch.StartNew();
        var reads = Enumerable.Range(0, 50).Select(_ => database.ReadAsync(async sql => await sql.ScalarAsync("SELECT id FROM users WHERE id=$1", user.Id))).ToArray();
        var results = await Task.WhenAll(reads).WaitAsync(TimeSpan.FromSeconds(10));
        Assert.All(results, value => Assert.Equal(user.Id, value));
        output.WriteLine($"50 concurrent async reads: {watch.Elapsed.TotalMilliseconds:F3}ms; pending thread-pool work: {ThreadPool.PendingWorkItemCount}");
    }

    [PgFact]
    public async Task Dashboard_IsOwned_FastWithoutProviders_AndKeepsMissingEvidenceExplicit()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var database = new AccountDatabase(user.Connection);
        var service = new DashboardSummaryService(database, new PublicMarketSnapshot(TimeProvider.System), TimeProvider.System);
        var result = await service.Read(user.Id, CancellationToken.None);
        Assert.Equal(user.Id, result["user_id"]!.GetValue<string>());
        Assert.Equal(10000m, decimal.Parse(result["portfolio"]!["cash"]!.GetValue<string>(), System.Globalization.CultureInfo.InvariantCulture));
        Assert.Null(result["market"]);
        Assert.Contains("market.snapshot", result["unavailable"]!.AsArray().Select(item => item!.GetValue<string>()));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task SharedPublicSnapshots_AreReusableAcrossAccounts_AndColdReadsNeverWaitForWorkers()
    {
        using var user = new PgTestUser();
        using var another = new PgTestUser();
        var database = new AccountDatabase(user.Connection);
        var jobs = new ComputeJobRepository(user.Connection);
        var shared = new PublicSnapshotRepository(database);
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var configuration = new ConfigurationBuilder().Build();
        var service = new ComputeService(jobs, new AnalysisSnapshotRepository(database), configuration, cache, shared);
        var symbol = "111" + Random.Shared.Next(100, 999);
        var input = new { symbol };
        var payload = System.Text.Json.JsonSerializer.Serialize(input);
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        var error = await Assert.ThrowsAsync<ApplicationFailure>(() => service.ReadPublic(user.Id, "fund.detail", input, timeout.Token));
        Assert.Equal("public_snapshot_unavailable", error.Code);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        try
        {
            var id = await jobs.EnqueueAsync(user.Id, "fund.detail", payload, DateTime.UtcNow.AddSeconds(30));
            var claim = await jobs.ClaimAsync("shared-" + user.Id, ["fund.detail"]);
            Assert.NotNull(claim);
            Assert.Equal(id, claim.JobId);
            var result = new JsonObject { ["symbol"] = symbol, ["name"] = "Synthetic shared fund", ["nav_history"] = new JsonArray(), ["observed_at"] = DateTimeOffset.UtcNow.ToString("O"), ["stale"] = false };
            Assert.True(await jobs.FinishAsync(user.Id, id, claim.Attempt, claim.OwnerToken, result.ToJsonString(), null));
            var saved = await shared.ReadAsync("fund.detail", payload);
            Assert.NotNull(saved);
            var read = await service.ReadPublic(another.Id, "fund.detail", input, timeout.Token);
            Assert.Equal("hit", read.Cache);
            Assert.Equal(symbol, read.Data!["symbol"]!.GetValue<string>());
            Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", another.Id));
        }
        finally { user.Execute("DELETE FROM public_compute_snapshots WHERE kind='fund.detail' AND input_json=$1::jsonb", payload); }
    }

    [PgFact]
    public async Task UsageCounterContention_DoesNotHoldTheHttpResponse_AndStillRecordsTheRequest()
    {
        using var user = new PgTestUser();
        var day = DateTime.UtcNow.AddHours(8).ToString("yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture);
        user.Execute("INSERT INTO usage_daily(user_id,day,category,requests,errors,duration_ms,last_activity_at) VALUES($1,$2,'market',0,0,0,now())", user.Id, day);
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        factory.Services.GetRequiredService<PublicMarketSnapshot>().Publish(JsonNode.Parse(Market)!.AsObject(), DateTime.UtcNow);
        await using var connection = new Npgsql.NpgsqlConnection(user.Connection);
        await connection.OpenAsync();
        await using var transaction = await connection.BeginTransactionAsync();
        await using var command = new Npgsql.NpgsqlCommand("SELECT requests FROM usage_daily WHERE user_id=$1 AND day=$2 AND category='market' FOR UPDATE", connection, transaction);
        command.Parameters.AddWithValue(user.Id);
        command.Parameters.AddWithValue(day);
        await command.ExecuteScalarAsync();
        try
        {
            // Keep the analytics row locked: the complete response must still arrive.
            var response = await client.GetAsync("/api/v1/market/snapshot").WaitAsync(TimeSpan.FromSeconds(2));
            Assert.Equal(HttpStatusCode.OK, response.StatusCode);
            Assert.NotEmpty(await response.Content.ReadAsStringAsync());
        }
        finally { await transaction.CommitAsync(); }
        for (var attempt = 0; attempt < 100 && Convert.ToInt64(user.Query("SELECT requests FROM usage_daily WHERE user_id=$1 AND day=$2 AND category='market'", user.Id, day)) == 0; attempt++)
            await Task.Delay(20);
        Assert.Equal(1L, Convert.ToInt64(user.Query("SELECT requests FROM usage_daily WHERE user_id=$1 AND day=$2 AND category='market'", user.Id, day)));
    }

    [PgFact]
    public async Task SnapshotHttp_StillAuthenticatesEveryCacheHit_AndPropagatesTraceIds()
    {
        using var user = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        const string trace = "abcdef0123456789abcdef0123456789";
        client.DefaultRequestHeaders.Add("X-Trace-Id", trace);
        factory.Services.GetRequiredService<PublicMarketSnapshot>().Publish(JsonNode.Parse(Market)!.AsObject(), DateTime.UtcNow);
        var response = await client.GetAsync("/api/v1/market/snapshot");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(trace, response.Headers.GetValues("X-Trace-Id").Single());
        var session = await client.GetFromJsonAsync<JsonObject>("/api/v1/auth/session");
        Assert.False(string.IsNullOrWhiteSpace(session!["cache_scope"]!.GetValue<string>()));
        user.Execute("UPDATE users SET status='disabled' WHERE id=$1", user.Id);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/v1/market/snapshot")).StatusCode);
    }
}
