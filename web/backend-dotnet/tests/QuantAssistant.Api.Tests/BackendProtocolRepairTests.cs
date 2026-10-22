using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class BackendProtocolRepairTests
{
    // Complete production worker advertisement, including the 51st capability.
    internal static readonly string[] WorkerCapabilities =
    [
        "advisor.preflight", "advisor.report", "agent.research", "analysis.backtest", "analysis.benchmark", "analysis.brinson",
        "analysis.data_status", "analysis.decision", "analysis.explain", "analysis.funds", "analysis.lifecycle", "analysis.price_dates",
        "analysis.profit", "analysis.readiness", "analysis.stocks", "analysis.style_drift", "analysis.valuation_alerts", "announcements.fetch",
        "calendar.generate_next_year", "cost.positions", "dca.smart_plan_backtest", "fund.catalog", "fund.catalog_validate", "fund.curves",
        "fund.detail", "fund.execution_replay", "fund.health_check", "fund.nav_sources", "fund.public_observations", "fund.redemption_windows",
        "fund.risk", "fund.study", "fund.study_update", "fund.tracking", "fund.valuations", "goal.project", "ledger.replay", "market.history",
        "market.news", "market.quotes", "market.snapshot", "photo.identify", "photo.recognize", "portfolio.backtest", "research.fetch",
        "risk.metrics", "risk.stress_test", "sentiment.fetch", "strategy.preview", "strategy.view", "valuation.percentiles",
    ];

    [PgFact]
    public async Task WorkerClaim_AcceptsTheCompleteAdvertisementAndLeasesNewOperations()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        var id = await jobs.EnqueueAsync(user.Id, "goal.project", "{}", DateTime.UtcNow.AddMinutes(2));
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) =>
            config.AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = new string('s', 32) })));
        using var client = factory.CreateTestClient();
        client.DefaultRequestHeaders.Add("X-Quant-Worker-Secret", new string('s', 32));
        var response = await client.PostAsJsonAsync("/internal/v1/worker/claim", new { worker_id = "complete-" + user.Id, capabilities = WorkerCapabilities });
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
        var lease = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(id, lease.GetProperty("job_id").GetString());
        Assert.Equal("goal.project", lease.GetProperty("kind").GetString());
        Assert.Equal(user.Id, lease.GetProperty("user_id").GetString());
        Assert.Equal(51L, user.Query("SELECT jsonb_array_length(capabilities)::bigint FROM worker_heartbeats WHERE worker_id=$1", "complete-" + user.Id));
        await Assert.ThrowsAsync<ArgumentException>(() => jobs.ClaimAsync("invalid", ["market.snapshot", "untrusted.operation"]));
    }

    [PgFact]
    public async Task EnqueueOwned_AcceptsEveryImplementedWorkerOperation()
    {
        using var user = new PgTestUser();
        await new AccountDatabase(user.Connection).WriteNonLedgerAsync(user.Id, sql =>
        {
            foreach (var kind in WorkerCapabilities)
                ComputeJobRepository.EnqueueOwned(sql, user.Id, kind, "{}", DateTime.UtcNow.AddMinutes(2));
            return Task.FromResult(0);
        });
        Assert.Equal(51L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task CompletedPublicObservations_AreReusedWithinTheirFreshnessWindow()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        foreach (var kind in new[] { "valuation.percentiles", "fund.health_check", "announcements.fetch" })
        {
            var first = await jobs.EnqueueAsync(user.Id, kind, "{}", DateTime.UtcNow.AddSeconds(60));
            var lease = (await jobs.ClaimAsync("public-" + user.Id, [kind]))!;
            Assert.True(await jobs.FinishAsync(user.Id, first, lease.Attempt, lease.OwnerToken, "{\"items\":[]}", null));
            // The execution deadline is not the observation's freshness lifetime.
            user.Execute("UPDATE compute_jobs SET deadline=now()-interval '1 second' WHERE user_id=$1 AND job_id=$2", user.Id, first);
            Assert.Equal(first, await jobs.EnqueueAsync(user.Id, kind, "{}", DateTime.UtcNow.AddMinutes(2)));
        }
    }
}
