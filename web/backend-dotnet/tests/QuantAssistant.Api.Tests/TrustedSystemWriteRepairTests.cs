using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class TrustedSystemWriteRepairTests
{
    [PgFact]
    public async Task ModelCache_MemoryFallbackSeparatesAccountsAndRejectsDisabledUsers()
    {
        using var owner = new PgTestUser();
        using var other = new PgTestUser();
        var prompt = new JsonObject { ["question"] = "synthetic-private-question" };
        var database = new AccountDatabase(owner.Connection);
        var cache = new ModelCallCacheService(database, new QuantOptions(), NullLogger<ModelCallCacheService>.Instance);
        owner.Execute("ALTER TABLE model_call_cache RENAME TO branch_repair_saved_model_call_cache");
        try
        {
            await cache.SetAsync(owner.Id, "synthetic/v1", prompt, new JsonObject { ["answer"] = "private" });
            Assert.True((await cache.TryGetAsync(owner.Id, "synthetic/v1", prompt)).Hit);
            Assert.False((await cache.TryGetAsync(other.Id, "synthetic/v1", prompt)).Hit);
            owner.Execute("UPDATE users SET status='disabled' WHERE id=$1", owner.Id);
            await Assert.ThrowsAsync<AuthFailure>(() => cache.TryGetAsync(owner.Id, "synthetic/v1", prompt));
            await Assert.ThrowsAsync<AuthFailure>(() => cache.SetAsync("model:cache", "synthetic/v1", prompt, new JsonObject()));
        }
        finally { owner.Execute("ALTER TABLE branch_repair_saved_model_call_cache RENAME TO model_call_cache"); }
    }

    [PgFact]
    public async Task ExplanationCache_CannotReturnAnotherAccountsSameRunId()
    {
        using var owner = new PgTestUser();
        using var other = new PgTestUser();
        const string run = "synthetic-shared-run-name";
        var prompt = new JsonObject { ["decision_run_id"] = run, ["user_text"] = "explain" };
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", owner.Connection);
        using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) =>
            config.AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = "" })));
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, owner.Connection, other.Id);
        try
        {
            foreach (var user in new[] { owner, other })
                user.Execute("INSERT INTO strategy_runs(user_id,run_id,as_of_date,generated_at,strategy_version,data_status,readiness,status) VALUES($1,$2,current_date,now(),'synthetic','valid','research_only','completed')", user.Id, run);
            var cache = factory.Services.GetRequiredService<ModelCallCacheService>();
            await cache.SetAsync(owner.Id, "analysis.explain/v1", prompt, new JsonObject { ["answer"] = "owners private explanation" });
            var response = await client.PostAsJsonAsync("/api/v1/analysis/explain", new { decision_run_id = run, user_text = "explain" });
            Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
            var body = await response.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal("worker_unavailable", body.GetProperty("error_code").GetString());
        }
        finally
        {
            foreach (var user in new[] { owner, other }) user.Execute("DELETE FROM strategy_runs WHERE user_id=$1", user.Id);
            owner.Execute("DELETE FROM model_call_cache WHERE prompt_hash=$1", ModelCallCacheService.CacheKey(owner.Id, "analysis.explain/v1", prompt));
        }
    }

    [PgFact]
    public async Task PublicIngestAndModelCache_PersistWithoutFictitiousUsers()
    {
        using var user = new PgTestUser();
        var database = new AccountDatabase(user.Connection);
        var unique = Guid.NewGuid().ToString("N");
        var prompt = new JsonObject { ["question"] = unique };
        var cache = new ModelCallCacheService(database, new QuantOptions(), NullLogger<ModelCallCacheService>.Instance);
        var key = ModelCallCacheService.CacheKey(user.Id, "synthetic/v1", prompt);
        try
        {
            var announcement = new JsonObject { ["fund_code"] = "510300", ["title"] = unique, ["kind"] = "other", ["source_url"] = "https://example.invalid/" + unique };
            var research = new JsonObject { ["symbol"] = "510300", ["title"] = unique, ["kind"] = "report", ["url"] = "https://example.invalid/" + unique };
            Assert.Equal(1, await new AnnouncementRepository(database).InsertBatchAsync([announcement]));
            Assert.Equal(0, await new AnnouncementRepository(database).InsertBatchAsync([announcement]));
            Assert.Equal(1, await new ResearchItemRepository(database).InsertBatchAsync([research]));
            Assert.Equal(0, await new ResearchItemRepository(database).InsertBatchAsync([research]));
            await cache.SetAsync(user.Id, "synthetic/v1", prompt, new JsonObject { ["answer"] = "saved" });
            var (hit, result) = await cache.TryGetAsync(user.Id, "synthetic/v1", prompt);
            Assert.True(hit);
            Assert.Equal("saved", result!["answer"]!.GetValue<string>());
            Assert.Equal(1L, user.Query("SELECT hits::bigint FROM model_call_cache WHERE prompt_hash=$1", key));
        }
        finally
        {
            user.Execute("DELETE FROM fund_announcements WHERE title=$1", unique);
            user.Execute("DELETE FROM research_items WHERE title=$1", unique);
            user.Execute("DELETE FROM model_call_cache WHERE prompt_hash=$1", key);
        }
    }

    [PgFact]
    public async Task AccountWrites_RejectInactiveAndArbitraryServiceIdentities()
    {
        using var user = new PgTestUser();
        var database = new AccountDatabase(user.Connection);
        user.Execute("UPDATE users SET status='disabled' WHERE id=$1", user.Id);
        foreach (var id in new[] { user.Id, "system:announcements", "system:research", "model:cache", "email:dispatch", "compute:reaper", "arbitrary" })
        {
            await Assert.ThrowsAsync<AuthFailure>(() => database.WriteAsync(id, _ => Task.FromResult(1)));
            await Assert.ThrowsAsync<AuthFailure>(() => database.WriteNonLedgerAsync(id, _ => Task.FromResult(1)));
        }
    }

    [PgFact]
    public void ReportCursor_VisitsEverySameTimestampNotificationOnceAcrossBatches()
    {
        using var user = new PgTestUser();
        var cursor = user.Query("SELECT value FROM site_settings WHERE key='email.dispatch.report_cursor'") as string;
        var timestamp = DateTime.UtcNow.AddMinutes(-1);
        try
        {
            user.Execute("INSERT INTO site_settings(key,value) VALUES('email.dispatch.report_cursor',$1) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", timestamp.AddDays(-1).ToString("O"));
            for (var index = 0; index < 65; index++)
            {
                var id = user.Id + index.ToString("D3");
                user.Execute("INSERT INTO market_advisor_reports(user_id,report_id,report_date,kind,status,created_at,updated_at) VALUES($1,$2,current_date,'morning','completed',$3,$3)", user.Id, id, timestamp);
                user.Execute("INSERT INTO advisor_notifications(user_id,id,kind,report_id,status,created_at) VALUES($1,$2,'morning',$2,'completed',$3)", user.Id, id, timestamp);
            }
            var service = new EmailDeliveryService(new AccountDatabase(user.Connection), new QuantOptions(), NullLogger<EmailDeliveryService>.Instance);
            service.CheckDispatch();
            Assert.Equal(50L, user.Query("SELECT count(*) FROM email_send_log WHERE user_id=$1", user.Id));
            service.CheckDispatch();
            Assert.Equal(65L, user.Query("SELECT count(*) FROM email_send_log WHERE user_id=$1", user.Id));
            service.CheckDispatch();
            Assert.Equal(65L, user.Query("SELECT count(*) FROM email_send_log WHERE user_id=$1", user.Id));
        }
        finally
        {
            user.Execute("DELETE FROM advisor_notifications WHERE user_id=$1", user.Id);
            user.Execute("DELETE FROM market_advisor_reports WHERE user_id=$1", user.Id);
            user.Execute("DELETE FROM email_send_log WHERE user_id=$1", user.Id);
            user.Execute("DELETE FROM site_settings WHERE key LIKE $1", "email.dispatch.report_sent:" + user.Id + ":%");
            if (cursor is null) user.Execute("DELETE FROM site_settings WHERE key='email.dispatch.report_cursor'");
            else user.Execute("UPDATE site_settings SET value=$1 WHERE key='email.dispatch.report_cursor'", cursor);
        }
    }
}
