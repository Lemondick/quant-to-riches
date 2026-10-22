using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class WorkerContractRepairTests
{
    private static WebApplicationFactory<Program> Factory(string connection)
    {
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", connection);
        return new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) =>
            config.AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = new string('s', 32) })));
    }

    private static async Task<ComputeJob> Claim(ComputeJobRepository jobs, string kind)
    {
        for (var attempt = 0; attempt < 200; attempt++)
        {
            if (await jobs.ClaimAsync("repair-" + kind, [kind]) is { } job) return job;
            await Task.Delay(25);
        }
        throw new Xunit.Sdk.XunitException("Expected queued operation: " + kind);
    }

    private static Task<bool> Finish(ComputeJobRepository jobs, ComputeJob job, string result) =>
        jobs.FinishAsync(job.UserId, job.JobId, job.Attempt, job.OwnerToken, result, null);

    [PgFact]
    public async Task PortfolioRequest_AllowsWeeklyDcaWithoutRebalance()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var request = client.PostAsJsonAsync("/api/v1/backtest/portfolio", new
        {
            funds = new[] { new { code = "510300", weight = 100 } },
            start = "2025-01-01", end = "2026-01-01", dca = new { amount = 100, freq = "weekly" },
        });
        var jobs = new ComputeJobRepository(user.Connection);
        var job = await Claim(jobs, "portfolio.backtest");
        var payload = JsonNode.Parse(job.InputJson)!;
        Assert.Null(payload["rebalance"]);
        Assert.Equal("weekly", payload["dca"]!["freq"]!.GetValue<string>());
        Assert.True(await Finish(jobs, job, "{\"metrics\":{\"total_invested\":\"5200\"}}"));
        var response = await request;
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
    }

    [PgFact]
    public async Task RiskRequests_IncludeOwnedSnapshotAndCachePerAuthenticatedUser()
    {
        using var first = new PgTestUser();
        using var second = new PgTestUser();
        using var factory = Factory(first.Connection);
        using var a = factory.CreateTestClient();
        using var b = factory.CreateTestClient();
        PgTestUser.AttachSession(a, first.Connection, first.Id);
        PgTestUser.AttachSession(b, second.Connection, second.Id);
        var jobs = new ComputeJobRepository(first.Connection);
        foreach (var (client, owner) in new[] { (a, first.Id), (b, second.Id) })
        {
            var request = client.GetAsync("/api/v1/risk/metrics?portfolio_id=primary");
            var job = await Claim(jobs, "risk.metrics");
            var payload = JsonNode.Parse(job.InputJson)!;
            Assert.Equal(owner, payload["snapshot"]?["user_id"]?.GetValue<string>());
            Assert.NotNull(payload["snapshot"]?["tables"]?["portfolio_valuations"]);
            Assert.True(await Finish(jobs, job, new JsonObject { ["owner"] = owner }.ToJsonString()));
            var response = await request;
            Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
            var result = await response.Content.ReadFromJsonAsync<JsonObject>();
            Assert.Equal(owner, result!["result"]!["owner"]!.GetValue<string>());
        }
        var stress = a.GetAsync("/api/v1/risk/stress");
        var stressJob = await Claim(jobs, "risk.stress_test");
        Assert.Equal(first.Id, JsonNode.Parse(stressJob.InputJson)!["snapshot"]?["user_id"]?.GetValue<string>());
        Assert.True(await Finish(jobs, stressJob, "{\"scenarios\":[]}"));
        Assert.True((await stress).IsSuccessStatusCode);
    }

    [PgFact]
    public async Task SmartDcaBacktest_ConvertsSavedFractionOnceAndKeepsAdjustments()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        var plans = factory.Services.GetRequiredService<SmartDcaRepository>();
        var service = factory.Services.GetRequiredService<SmartDcaService>();
        var rules = JsonNode.Parse("{\"low_pct_boost\":2,\"high_pct_cut\":0.9,\"halt_pct\":0.95}")!.AsObject();
        var plan = await plans.CreateAsync(user.Id, "000300", 100m, rules, "monthly", null);
        try
        {
            var result = service.BacktestAsync(user.Id, plan["id"]!.GetValue<string>(), new DateOnly(2025, 1, 1), new DateOnly(2026, 1, 1), default);
            var jobs = new ComputeJobRepository(user.Connection);
            var job = await Claim(jobs, "dca.smart_plan_backtest");
            var sent = JsonNode.Parse(job.InputJson)!["rules"]!;
            Assert.Equal(95d, sent["halt_pct"]!.GetValue<double>());
            Assert.Equal(2d, sent["low_pct_boost"]!.GetValue<double>());
            Assert.Equal(0.9d, sent["high_pct_cut"]!.GetValue<double>());
            Assert.True(await Finish(jobs, job, "{\"metrics\":{}}"));
            Assert.NotNull(await result);
            Assert.Equal(0.95d, (await plans.GetAsync(user.Id, plan["id"]!.GetValue<string>()))["rules"]!["halt_pct"]!.GetValue<double>());
        }
        finally { user.Execute("DELETE FROM smart_dca_plans WHERE user_id=$1", user.Id); }
    }

    [PgFact]
    public async Task SmartDcaReminder_UsesTheSamePercentileScaleAsBacktest()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        var plans = factory.Services.GetRequiredService<SmartDcaRepository>();
        var service = factory.Services.GetRequiredService<SmartDcaService>();
        var rules = JsonNode.Parse("{\"low_pct_boost\":2,\"high_pct_cut\":0.9,\"halt_pct\":0.95}")!.AsObject();
        await plans.CreateAsync(user.Id, "000300", 100m, rules, "monthly", null);
        try
        {
            var reminder = Task.Run(service.CheckReminders);
            var jobs = new ComputeJobRepository(user.Connection);
            var job = await Claim(jobs, "valuation.percentiles");
            Assert.True(await Finish(jobs, job, "{\"items\":[{\"symbol\":\"000300\",\"pe_pct\":80}]}"));
            await reminder.WaitAsync(TimeSpan.FromSeconds(10));
            var detail = JsonNode.Parse((string)user.Query("SELECT detail::text FROM user_alert_center WHERE user_id=$1 AND kind='smart_dca_reminder'", user.Id)!)!;
            Assert.Equal(10m, detail["suggested_amount"]!.GetValue<decimal>());
            Assert.Equal("高估削减", detail["trigger"]!.GetValue<string>());
        }
        finally
        {
            user.Execute("DELETE FROM user_alert_center WHERE user_id=$1", user.Id);
            user.Execute("DELETE FROM smart_dca_plans WHERE user_id=$1", user.Id);
        }
    }

    [PgFact]
    public async Task RedemptionReminder_SendsVerifiedTiersAndReadsTierObjects()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        var today = FundFeeRepository.Today();
        var database = factory.Services.GetRequiredService<AccountDatabase>();
        var fees = factory.Services.GetRequiredService<FundFeeRepository>();
        fees.Save(user.Id, new FundFeeSchedule("510300", "A", 0, 0,
            [new(0, 6, 0.015m), new(7, null, 0m)], today, today.AddDays(-20), EvidenceReference: "synthetic evidence"));
        database.WriteNonLedger(user.Id, sql =>
        {
            var id = sql.Scalar("INSERT INTO fund_lot_snapshots(user_id,symbol,position_fingerprint,request_fingerprint,verified_on,evidence_reference,created_at) VALUES($1,'510300','p','r',$2,'synthetic',now()) RETURNING id", user.Id, today)!;
            sql.Execute("INSERT INTO fund_holding_lots(user_id,snapshot_id,lot_id,confirmed_on,remaining_units) VALUES($1,$2,'lot',$3,100)", user.Id, id, today.AddDays(-6));
            sql.Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date) VALUES($1,'510300','fund',100,1,200,$2)", user.Id, today);
            return 0;
        });
        try
        {
            var reminder = Task.Run(factory.Services.GetRequiredService<FundMaintenanceService>().CheckRedemptionReminders);
            var jobs = new ComputeJobRepository(user.Connection);
            var job = await Claim(jobs, "fund.redemption_windows");
            var lot = JsonNode.Parse(job.InputJson)!["lots"]![0]!;
            Assert.Equal(0.015m, decimal.Parse(lot["redemption_tiers"]![0]!["rate"]!.ToString(), System.Globalization.CultureInfo.InvariantCulture));
            Assert.Equal(200m, decimal.Parse(lot["amount"]!.GetValue<string>(), System.Globalization.CultureInfo.InvariantCulture));
            Assert.True(await Finish(jobs, job, "{\"windows\":[{\"fund_code\":\"510300\",\"lot_id\":\"lot\",\"current_tier\":{\"min_days\":0,\"max_days\":6,\"rate\":\"0.015\"},\"next_tier\":{\"min_days\":7,\"max_days\":null,\"rate\":\"0\"},\"next_tier_at\":\"2026-10-23\",\"days_left\":1,\"save_estimate\":\"3.00\"}]}"));
            await reminder.WaitAsync(TimeSpan.FromSeconds(10));
            var detail = JsonNode.Parse((string)user.Query("SELECT detail::text FROM user_alert_center WHERE user_id=$1 AND kind='redemption_window'", user.Id)!)!;
            Assert.Equal("0.015", detail["current_tier"]!["rate"]!.GetValue<string>());
            Assert.Contains("1.5%", detail["message"]!.GetValue<string>());
        }
        finally
        {
            foreach (var table in new[] { "user_alert_center", "fund_holding_lots", "fund_lot_snapshots", "fund_fee_schedules" })
                user.Execute($"DELETE FROM {table} WHERE user_id=$1", user.Id);
        }
    }
}
