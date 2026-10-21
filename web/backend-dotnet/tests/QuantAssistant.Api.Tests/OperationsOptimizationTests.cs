using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class OperationsOptimizationTests
{
    private static WebApplicationFactory<Program> Factory(PgTestUser user, Dictionary<string, string?>? options = null)
    {
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        return new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) =>
            config.AddInMemoryCollection(options ?? new Dictionary<string, string?>())));
    }
    private static async Task<JsonElement> Json(HttpResponseMessage response)
    {
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
        return await response.Content.ReadFromJsonAsync<JsonElement>();
    }

    [PgFact]
    public async Task ReadinessRequiresExpectedWorkers_StaleAndStoppingHeartbeatsDoNotCount()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user, new() { ["QUANT_EXPECTED_WORKERS"] = "3", ["QUANT_WORKER_SECRET"] = new string('x', 40) });
        using var client = factory.CreateTestClient();
        // API startup already marks old heartbeats as stopping in this isolated database.
        var prefix = "optimization-" + user.Id;
        user.Execute("UPDATE worker_heartbeats SET stopping=true");
        try
        {
            user.Execute("INSERT INTO worker_heartbeats(worker_id,capabilities,last_seen_at,stopping) VALUES($1,'[]',now(),false),($2,'[]',now()-interval '1 minute',false),($3,'[]',now(),true)", prefix + "1", prefix + "2", prefix + "3");
            Assert.Equal(HttpStatusCode.ServiceUnavailable, (await client.GetAsync("/readyz")).StatusCode);
            user.Execute("UPDATE worker_heartbeats SET last_seen_at=now(),stopping=false WHERE worker_id LIKE $1", prefix + "%");
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/readyz")).StatusCode);
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/healthz")).StatusCode);
        }
        finally { user.Execute("DELETE FROM worker_heartbeats WHERE worker_id LIKE $1", prefix + "%"); }
    }

    [PgFact]
    public async Task RuntimeExposesDegradationQueueDiskAndBackupAge_WithoutFinancialWrites()
    {
        using var user = new PgTestUser();
        user.Execute("UPDATE users SET role='admin' WHERE id=$1", user.Id);
        using var factory = Factory(user, new() { ["QUANT_EXPECTED_WORKERS"] = "3", ["QUANT_BACKUP_DIR"] = "", ["QUANT_DISK_MIN_FREE_PERCENT"] = "100" });
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        user.Execute("INSERT INTO compute_jobs(user_id,job_id,kind,input_json,input_hash,status,deadline,created_at) VALUES($1,'old-queued','market.quotes','{}','x','queued',now()+interval '1 hour',now()-interval '10 minutes'),($1,'overdue','market.quotes','{}','y','running',now()-interval '1 minute',now()-interval '2 minutes')", user.Id);
        var runtime = await Json(await client.GetAsync("/api/v1/admin/runtime"));
        var operations = runtime.GetProperty("operations");
        Assert.Equal(3, operations.GetProperty("workers").GetProperty("expected").GetInt32());
        Assert.Equal("degraded", operations.GetProperty("status").GetString());
        Assert.True(operations.GetProperty("queue").GetProperty("queued").GetInt64() >= 1);
        Assert.True(operations.GetProperty("queue").GetProperty("overdue").GetInt64() >= 1);
        var codes = operations.GetProperty("alerts").EnumerateArray().Select(item => item.GetProperty("code").GetString()).ToArray();
        Assert.Contains("queue_waiting", codes);
        Assert.Contains("task_overdue", codes);
        Assert.Contains("backup_not_configured", codes);
        Assert.Contains("disk_low", codes);
        Assert.DoesNotContain(user.Connection, operations.ToString());
        Assert.Equal(0L, user.Query("SELECT count(*) FROM transactions WHERE user_id=$1", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM portfolio_events WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task CalendarReadImportAndVersionConflict_AreAdminOnlyAndYearValidated()
    {
        using var admin = new PgTestUser();
        using var other = new PgTestUser();
        admin.Execute("UPDATE users SET role='admin' WHERE id=$1", admin.Id);
        using var factory = Factory(admin);
        using var client = factory.CreateTestClient();
        using var ordinary = factory.CreateTestClient();
        PgTestUser.AttachSession(client, admin.Connection, admin.Id);
        PgTestUser.AttachSession(ordinary, admin.Connection, other.Id);
        const int year = 2099;
        var old = admin.Query("SELECT value FROM site_settings WHERE key='calendar:2099'") as string;
        admin.Execute("DELETE FROM site_settings WHERE key='calendar:2099'");
        try
        {
            var missing = await Json(await client.GetAsync("/api/v1/admin/calendar?year=2099"));
            Assert.False(missing.GetProperty("configured").GetBoolean());
            var body = new { year, source = "https://www.sse.com.cn/synthetic-test", holidays = new[] { "2099-01-01", "2099-01-01" }, expected_revision = missing.GetProperty("revision").GetString() };
            var saved = await Json(await client.PutAsJsonAsync("/api/v1/admin/calendar", body));
            Assert.Single(saved.GetProperty("holidays").EnumerateArray());
            var read = await Json(await client.GetAsync("/api/v1/admin/calendar?year=2099"));
            Assert.True(read.GetProperty("configured").GetBoolean());
            Assert.Equal(saved.GetProperty("revision").GetString(), read.GetProperty("revision").GetString());
            Assert.Equal(HttpStatusCode.Conflict, (await client.PutAsJsonAsync("/api/v1/admin/calendar", body)).StatusCode);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PutAsJsonAsync("/api/v1/admin/calendar", new { year, source = body.source, holidays = new[] { "2098-01-01" } })).StatusCode);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PutAsJsonAsync("/api/v1/admin/calendar", new { year, source = body.source, holidays = (string[]?)null })).StatusCode);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PutAsJsonAsync("/api/v1/admin/calendar", new { year, source = "https://example.org/calendar", holidays = body.holidays })).StatusCode);
            Assert.Equal(HttpStatusCode.Forbidden, (await ordinary.GetAsync("/api/v1/admin/calendar?year=2099")).StatusCode);
            Assert.Equal(HttpStatusCode.Forbidden, (await ordinary.PutAsJsonAsync("/api/v1/admin/calendar", body)).StatusCode);
            var overview = await Json(await client.GetAsync("/api/v1/admin/calendar/status"));
            Assert.True(overview.GetProperty("current").GetProperty("configured").GetBoolean());
            Assert.True(overview.TryGetProperty("next", out _));
        }
        finally
        {
            admin.Execute("DELETE FROM admin_audit WHERE actor_id=$1", admin.Id);
            if (old is null) admin.Execute("DELETE FROM site_settings WHERE key='calendar:2099'");
            else admin.Execute("UPDATE site_settings SET value=$1 WHERE key='calendar:2099'", old);
        }
    }

    [PgFact]
    public async Task DamagedBackupStatusIsAnAlert_NotARuntimeFailure()
    {
        using var user = new PgTestUser();
        user.Execute("UPDATE users SET role='admin' WHERE id=$1", user.Id);
        var directory = Path.Combine(Path.GetTempPath(), "quant-backup-state-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            using var factory = Factory(user, new() { ["QUANT_BACKUP_DIR"] = directory });
            using var client = factory.CreateTestClient();
            PgTestUser.AttachSession(client, user.Connection, user.Id);
            foreach (var invalid in new[] { "null", "{bad json", "{\"status\":1}" })
            {
                File.WriteAllText(Path.Combine(directory, ".native-backup-status.json"), invalid);
                var response = await client.GetAsync("/api/v1/admin/runtime");
                Assert.True(response.IsSuccessStatusCode, invalid + ": " + await response.Content.ReadAsStringAsync());
                var runtime = await Json(response);
                Assert.Contains(runtime.GetProperty("operations").GetProperty("alerts").EnumerateArray(), alert => alert.GetProperty("code").GetString() == "backup_automation_failed");
            }
        }
        finally { Directory.Delete(directory, true); }
    }
}
