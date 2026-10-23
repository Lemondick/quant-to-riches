using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class ProjectOptimizationPhase2Tests
{
    private static WebApplicationFactory<Program> Factory(string connection)
    {
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", connection);
        return new WebApplicationFactory<Program>().WithWebHostBuilder(builder =>
            builder.ConfigureAppConfiguration((_, configuration) => configuration.AddInMemoryCollection(
                new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = new string('s', 32) })));
    }

    private static async Task<JsonElement> Json(HttpResponseMessage response)
    {
        Assert.True(response.IsSuccessStatusCode, $"{response.StatusCode}: {await response.Content.ReadAsStringAsync()}");
        return await response.Content.ReadFromJsonAsync<JsonElement>();
    }

    [PgFact]
    public async Task NetWorth_ExplicitlyMarksUnrecordedLiabilitiesAndPositionsScope()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var summary = await Json(await client.GetAsync("/api/v1/net-worth"));
        Assert.False(summary.GetProperty("liabilities_recorded").GetBoolean());
        Assert.Equal("positions_only", summary.GetProperty("calculation_scope").GetString());
        Assert.Equal(summary.GetProperty("total_assets").GetString(), summary.GetProperty("net_worth").GetString());
        Assert.Contains("负债", summary.GetProperty("liabilities_note").GetString());
    }

    private static object PortfolioBody(decimal weight = 100) => new
    {
        funds = new[] { new { code = "510300", weight } }, start = "2025-01-01", end = "2025-12-31",
    };

    private static Task<string> Enqueue(PgTestUser user, string kind = "portfolio.backtest") =>
        new ComputeJobRepository(user.Connection).EnqueueAsync(user.Id, kind, "{}", DateTime.UtcNow.AddMinutes(10));

    [PgFact]
    public async Task BackgroundPortfolio_ValidatesThenReturnsDurableDeduplicatedTask()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        client.Timeout = TimeSpan.FromSeconds(4);
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        Assert.Equal(HttpStatusCode.UnprocessableEntity,
            (await client.PostAsJsonAsync("/api/v1/backtest/portfolio?background=true", PortfolioBody(90))).StatusCode);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        using var response = await client.PostAsJsonAsync("/api/v1/backtest/portfolio?background=true", PortfolioBody());
        Assert.Equal(HttpStatusCode.Accepted, response.StatusCode);
        var task = await Json(response);
        var id = task.GetProperty("job_id").GetString();
        Assert.Equal("queued", task.GetProperty("status").GetString());
        Assert.Equal("portfolio.backtest", task.GetProperty("kind").GetString());
        Assert.Equal($"/api/v1/compute/tasks/{id}", response.Headers.Location!.ToString());
        Assert.Equal(TimeSpan.FromSeconds(2), response.Headers.RetryAfter!.Delta);
        Assert.InRange(task.GetProperty("deadline").GetDateTimeOffset() - DateTimeOffset.UtcNow, TimeSpan.FromSeconds(590), TimeSpan.FromSeconds(601));
        var again = await Json(await client.PostAsJsonAsync("/api/v1/backtest/portfolio?background=true", PortfolioBody()));
        Assert.Equal(id, again.GetProperty("job_id").GetString());
        Assert.Equal(1L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        var read = await Json(await client.GetAsync($"/api/v1/compute/tasks/{id}"));
        Assert.Equal(id, read.GetProperty("job_id").GetString());
        Assert.Null(read.GetProperty("result").Deserialize<object>());
        Assert.False(read.TryGetProperty("input_json", out _));
        Assert.False(read.TryGetProperty("owner_token", out _));
    }

    [PgFact]
    public async Task Tasks_RejectOtherAccountsInternalKindsAnonymousAndMissingCsrf()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        using var foreign = factory.CreateTestClient();
        using var anonymous = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        PgTestUser.AttachSession(foreign, user.Connection, other.Id);
        var id = await Enqueue(user);
        var internalId = await Enqueue(user, "analysis.data_status");
        foreach (var hidden in new[] { id, internalId })
        {
            Assert.Equal(HttpStatusCode.NotFound, (await foreign.GetAsync($"/api/v1/compute/tasks/{hidden}")).StatusCode);
            Assert.Equal(HttpStatusCode.NotFound, (await foreign.DeleteAsync($"/api/v1/compute/tasks/{hidden}")).StatusCode);
        }
        Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync($"/api/v1/compute/tasks/{internalId}")).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await client.DeleteAsync($"/api/v1/compute/tasks/{internalId}")).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, (await anonymous.GetAsync($"/api/v1/compute/tasks/{id}")).StatusCode);
        client.DefaultRequestHeaders.Remove("X-CSRF-Token");
        Assert.Equal(HttpStatusCode.Forbidden, (await client.DeleteAsync($"/api/v1/compute/tasks/{id}")).StatusCode);
        Assert.Equal("queued", user.Query("SELECT status FROM compute_jobs WHERE user_id=$1 AND job_id=$2", user.Id, id));
    }

    [PgTheory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task Cancel_IsDurableIdempotentAndRejectsLateWorker(bool running)
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var repo = new ComputeJobRepository(user.Connection);
        var id = await Enqueue(user);
        var lease = running ? await repo.ClaimAsync("synthetic-phase2-worker", ["portfolio.backtest"]) : null;
        if (running) Assert.Equal(id, lease!.JobId);
        var state = await Json(await client.DeleteAsync($"/api/v1/compute/tasks/{id}"));
        Assert.Equal("cancelled", state.GetProperty("status").GetString());
        Assert.Equal("cancelled", (await Json(await client.DeleteAsync($"/api/v1/compute/tasks/{id}"))).GetProperty("status").GetString());
        Assert.Equal("cancelled", (await Json(await client.GetAsync($"/api/v1/compute/tasks/{id}"))).GetProperty("status").GetString());
        if (lease is not null)
        {
            Assert.False(await repo.HeartbeatAsync(user.Id, id, lease.Attempt, lease.OwnerToken));
            Assert.False(await repo.FinishAsync(user.Id, id, lease.Attempt, lease.OwnerToken, "{\"late\":true}", null));
        }
        Assert.Null((await repo.ReadAsync(user.Id, id)).ResultJson);
    }

    [PgFact]
    public async Task Cancel_DoesNotEraseCompletedResults()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var repo = new ComputeJobRepository(user.Connection);
        var id = await Enqueue(user);
        var lease = (await repo.ClaimAsync("synthetic-phase2-worker", ["portfolio.backtest"]))!;
        Assert.Equal(id, lease.JobId);
        Assert.True(await repo.FinishAsync(user.Id, id, lease.Attempt, lease.OwnerToken, "{\"synthetic_result\":42}", null));
        var state = await Json(await client.DeleteAsync($"/api/v1/compute/tasks/{id}"));
        Assert.Equal("completed", state.GetProperty("status").GetString());
        Assert.Equal(42, state.GetProperty("result").GetProperty("synthetic_result").GetInt32());
    }

    [PgTheory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task ExpiredDeadlineOrLease_IsReportedInterrupted(bool leaseExpired)
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var id = await Enqueue(user);
        user.Execute(leaseExpired
            ? "UPDATE compute_jobs SET status='running',lease_expires_at=now()-interval '1 second' WHERE user_id=$1 AND job_id=$2"
            : "UPDATE compute_jobs SET deadline=now()-interval '1 second' WHERE user_id=$1 AND job_id=$2", user.Id, id);
        var state = await Json(await client.GetAsync($"/api/v1/compute/tasks/{id}"));
        Assert.Equal("interrupted", state.GetProperty("status").GetString());
        Assert.Null(state.GetProperty("result").Deserialize<object>());
    }

    [PgFact]
    public async Task BackgroundValuation_UsesTheSameValidatedPayloadAndShortResponse()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        client.Timeout = TimeSpan.FromSeconds(4);
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.GetAsync("/api/v1/valuation?years=1&background=true")).StatusCode);
        using var response = await client.GetAsync("/api/v1/valuation?years=5&indices=000300:CN&background=true");
        Assert.Equal(HttpStatusCode.Accepted, response.StatusCode);
        var task = await Json(response);
        Assert.Equal("valuation.percentiles", task.GetProperty("kind").GetString());
        Assert.InRange(task.GetProperty("deadline").GetDateTimeOffset() - DateTimeOffset.UtcNow, TimeSpan.FromSeconds(170), TimeSpan.FromSeconds(181));
        Assert.Equal(5, (int)user.Query("SELECT (input_json->>'years')::int FROM compute_jobs WHERE user_id=$1", user.Id)!);
    }

    [PgFact]
    public async Task BackgroundSmartDca_ChecksPlanOwnershipAndPreservesThresholdScaling()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        using var foreign = factory.CreateTestClient();
        client.Timeout = TimeSpan.FromSeconds(4);
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        PgTestUser.AttachSession(foreign, user.Connection, other.Id);
        var plans = new SmartDcaRepository(new AccountDatabase(user.Connection));
        var plan = await plans.CreateAsync(user.Id, "510300", 100,
            JsonNode.Parse("""{"low_pct_boost":2,"high_pct_cut":0.5,"halt_pct":0.9}""")!.AsObject(), "monthly", new DateOnly(2026, 11, 1));
        var path = $"/api/v1/dca/smart/{plan["id"]!.GetValue<string>()}/backtest?background=true";
        var body = new { start = "2025-01-01", end = "2025-12-31" };
        Assert.Equal(HttpStatusCode.NotFound, (await foreign.PostAsJsonAsync(path, body)).StatusCode);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync(path, new { start = "2025-12-31", end = "2025-01-01" })).StatusCode);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        using var response = await client.PostAsJsonAsync(path, body);
        Assert.Equal(HttpStatusCode.Accepted, response.StatusCode);
        var task = await Json(response);
        Assert.Equal("dca.smart_plan_backtest", task.GetProperty("kind").GetString());
        Assert.Equal(90d, (double)user.Query("SELECT (input_json->'rules'->>'halt_pct')::double precision FROM compute_jobs WHERE user_id=$1", user.Id)!);
    }
}
