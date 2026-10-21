using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class AdvisorPreflightTests
{
    [PgFact]
    public async Task ConfiguredPreflight_HasAccountSnapshotWithoutSecrets_AndBudgetStopsBeforeQuota()
    {
        using var user = new PgTestUser();
        var root = Path.Combine(Path.GetTempPath(), "quant-preflight-secrets-" + Guid.NewGuid().ToString("N"));
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        try
        {
            using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) => config.AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_SECRET_ROOT"] = root, ["QUANT_WORKER_SECRET"] = "synthetic-preflight-worker-secret-1234" })));
            using var client = factory.CreateClient();
            PgTestUser.AttachSession(client, user.Connection, user.Id);
            var secrets = factory.Services.GetRequiredService<PrivateSecretStore>();
            foreach (var key in new[] { "model-provider", "model-jev" })
            {
                var reference = secrets.Save(user.Id, "synthetic-unused-model-credential");
                user.Execute("INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,$2,1,$3::jsonb)", user.Id, key, JsonSerializer.Serialize(new { revision=1, models=key == "model-provider" ? new[] { "gemini-3.1-pro-low", "gpt-6.1-sol", "gpt-6-astra" } : new[] { "jev-latest" }, key_reference=reference }));
            }
            user.Execute("INSERT INTO advisor_provider_selection(user_id,id,provider_revision) VALUES($1,1,1)", user.Id);
            var pending = client.PostAsJsonAsync("/api/v1/advisor/preflight", new { kind="morning" });
            var jobs = new ComputeJobRepository(user.Connection);
            ComputeJob? claim = null;
            for (var i=0; i<100 && claim is null && !pending.IsCompleted; i++) { claim=jobs.Claim("preflight-fixture", ["advisor.preflight"]); if(claim is null) await Task.Delay(30); }
            Assert.NotNull(claim);
            Assert.Equal(user.Id, JsonDocument.Parse(claim.InputJson).RootElement.GetProperty("snapshot").GetProperty("user_id").GetString());
            Assert.DoesNotContain("credential", claim.InputJson);
            Assert.DoesNotContain("key_reference", claim.InputJson);
            Assert.True(jobs.Finish(user.Id, claim.JobId, claim.Attempt, claim.OwnerToken, "{\"decision_ready\":false,\"issues\":[],\"source_count\":0,\"symbols\":[]}", null));
            Assert.Equal(HttpStatusCode.OK, (await pending).StatusCode);
            var costs = new ModelCostRepository(new AccountDatabase(user.Connection));
            costs.Save(user.Id, new(0, DailyBudget:1));
            var preflight = await client.PostAsJsonAsync("/api/v1/advisor/preflight", new { kind="morning" });
            var view = await preflight.Content.ReadFromJsonAsync<JsonElement>();
            Assert.False(view.GetProperty("can_generate").GetBoolean());
            Assert.Equal("unbounded_model_cost", view.GetProperty("issues")[0].GetProperty("code").GetString());
            Assert.Equal(HttpStatusCode.Conflict, (await client.PostAsJsonAsync("/api/v1/advisor/reports", new { kind="morning" })).StatusCode);
            Assert.Equal(0L, user.Query("SELECT count(*) FROM market_advisor_reports WHERE user_id=$1", user.Id));
            Assert.Equal(0L, user.Query("SELECT count(*) FROM model_call_costs WHERE user_id=$1", user.Id));
        }
        finally { user.Execute("DELETE FROM advisor_provider_selection WHERE user_id=$1", user.Id); if(Directory.Exists(root)) Directory.Delete(root, true); }
    }
    [PgFact]
    public async Task MissingConfiguration_ReportsRepairLink_WithoutPaidWork()
    {
        using var user = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var response = await client.PostAsJsonAsync("/api/v1/advisor/preflight", new { kind = "morning" });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.False(data.GetProperty("can_generate").GetBoolean());
        Assert.Equal(0, data.GetProperty("model_calls").GetInt32());
        var issue = Assert.Single(data.GetProperty("issues").EnumerateArray());
        Assert.Equal("model_configuration", issue.GetProperty("code").GetString());
        Assert.Contains("settings/index", issue.GetProperty("url").GetString());
        Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM market_advisor_reports WHERE user_id=$1", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM model_call_costs WHERE user_id=$1", user.Id));
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync("/api/v1/advisor/preflight", new { kind = "bad" })).StatusCode);
        using var anonymous = factory.CreateClient();
        Assert.Equal(HttpStatusCode.Unauthorized, (await anonymous.PostAsJsonAsync("/api/v1/advisor/preflight", new { kind = "morning" })).StatusCode);
    }
}
