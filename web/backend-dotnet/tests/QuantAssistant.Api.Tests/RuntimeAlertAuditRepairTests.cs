using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Npgsql;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class RuntimeAlertAuditRepairTests
{
    private static WebApplicationFactory<Program> Factory(PgTestUser user, bool worker = false)
    {
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        return new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) =>
            config.AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = worker ? new string('s', 32) : "" })));
    }

    [PgFact]
    public async Task AdminRuntime_RetainsDashboardEnvelopeAndAdminAuthorization()
    {
        using var admin = new PgTestUser();
        using var other = new PgTestUser();
        admin.Execute("UPDATE users SET role='admin' WHERE id=$1", admin.Id);
        using var factory = Factory(admin);
        using var client = factory.CreateTestClient();
        using var ordinary = factory.CreateTestClient();
        PgTestUser.AttachSession(client, admin.Connection, admin.Id);
        PgTestUser.AttachSession(ordinary, admin.Connection, other.Id);
        Assert.Equal(HttpStatusCode.Forbidden, (await ordinary.GetAsync("/api/v1/admin/runtime")).StatusCode);
        var response = await client.GetAsync("/api/v1/admin/runtime");
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(JsonValueKind.False, body.GetProperty("automatic_trading").ValueKind);
        Assert.Equal(JsonValueKind.Array, body.GetProperty("workers").ValueKind);
        Assert.Equal(JsonValueKind.Object, body.GetProperty("operations").ValueKind);
        Assert.Equal(JsonValueKind.False, body.GetProperty("scheduler_running").ValueKind);
        Assert.Contains(body.GetProperty("accounts").EnumerateArray(), row => row.GetProperty("user_id").GetString() == other.Id);
        Assert.Equal(body.GetProperty("operations").GetProperty("workers").GetProperty("status").GetString() == "healthy", body.GetProperty("business_ready").GetBoolean());
    }

    [PgFact]
    public async Task Alerts_UseAccountPriceDatesAndRetainUnknownEvidenceWhenWorkerUnavailable()
    {
        using var user = new PgTestUser();
        user.Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at) VALUES($1,'SYNTHETIC','stock',1,10,10,current_date,now())", user.Id);
        try
        {
            using (var offline = Factory(user))
            using (var client = offline.CreateTestClient())
            {
                PgTestUser.AttachSession(client, user.Connection, user.Id);
                var response = await client.GetAsync("/api/v1/alerts/center");
                Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
                var unknown = await response.Content.ReadFromJsonAsync<JsonElement>();
                Assert.Contains(unknown.GetProperty("items").EnumerateArray(), row => row.GetProperty("kind").GetString() == "data_unknown");
            }
            using var factory = Factory(user, worker: true);
            using var online = factory.CreateTestClient();
            PgTestUser.AttachSession(online, user.Connection, user.Id);
            var request = online.GetAsync("/api/v1/alerts/center");
            var jobs = new ComputeJobRepository(user.Connection);
            ComputeJob? lease = null;
            for (var attempt = 0; attempt < 100 && lease is null; attempt++)
            {
                lease = await jobs.ClaimAsync("alerts-" + user.Id, ["analysis.price_dates", "market.snapshot"]);
                if (lease is null) await Task.Delay(25);
            }
            Assert.NotNull(lease);
            Assert.Equal("analysis.price_dates", lease.Kind);
            var payload = JsonDocument.Parse(lease.InputJson).RootElement;
            Assert.Equal(user.Id, payload.GetProperty("snapshot").GetProperty("user_id").GetString());
            var today = DateTime.UtcNow.AddHours(8).ToString("yyyy-MM-dd");
            Assert.True(await jobs.FinishAsync(user.Id, lease.JobId, lease.Attempt, lease.OwnerToken, "{\"stock:SYNTHETIC\":\"" + today + "\"}", null));
            var freshResponse = await request;
            Assert.True(freshResponse.IsSuccessStatusCode, await freshResponse.Content.ReadAsStringAsync());
            var fresh = await freshResponse.Content.ReadFromJsonAsync<JsonElement>();
            Assert.DoesNotContain(fresh.GetProperty("items").EnumerateArray(), row => row.GetProperty("kind").GetString() is "data_unknown" or "data_stale");
        }
        finally { user.Execute("DELETE FROM user_alert_center WHERE user_id=$1", user.Id); }
    }

    [PgFact]
    public async Task Alerts_EvaluationPreservesScheduledRemindersAndClearsResolvedOwnedAlerts()
    {
        using var user = new PgTestUser();
        string[] scheduled = ["smart_dca_reminder", "redemption_window", "fund_health_check", "goal_deviation"];
        foreach (var kind in scheduled.Append("data_stale"))
            user.Execute("INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged) VALUES($1,$2,$3,'synthetic','{}'::json,now(),now(),true,true)", user.Id, Guid.NewGuid().ToString("N"), kind);
        try
        {
            using var factory = Factory(user);
            using var client = factory.CreateTestClient();
            PgTestUser.AttachSession(client, user.Connection, user.Id);
            var response = await client.GetAsync("/api/v1/alerts/center");
            Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
            var body = await response.Content.ReadFromJsonAsync<JsonElement>();
            var items = body.GetProperty("items").EnumerateArray().ToArray();
            Assert.Equal(scheduled.Order(), items.Select(row => row.GetProperty("kind").GetString()!).Order());
            Assert.All(items, row => Assert.True(row.GetProperty("acknowledged").GetBoolean()));
        }
        finally { user.Execute("DELETE FROM user_alert_center WHERE user_id=$1", user.Id); }
    }

    [PgFact]
    public async Task AuditAppend_ConcurrentRawAndAccountWritersKeepOneChain()
    {
        using var first = new PgTestUser();
        using var second = new PgTestUser();
        var action = "branch-repair-" + Guid.NewGuid().ToString("N");
        var database = new AccountDatabase(first.Connection);
        var audit = new AdminAuditRepository(database);
        first.Execute("CREATE FUNCTION branch_repair_audit_delay() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action LIKE 'branch-repair-%' THEN PERFORM pg_sleep(0.15); END IF; RETURN NEW; END $$");
        first.Execute("CREATE TRIGGER branch_repair_audit_delay BEFORE INSERT ON admin_audit FOR EACH ROW EXECUTE FUNCTION branch_repair_audit_delay()");
        try
        {
            database.WriteSystem(sql => { audit.Write(sql, first.Id, action, null, "seed", DateTime.UtcNow); return 0; });
            using var start = new ManualResetEventSlim();
            var tasks = Enumerable.Range(0, 8).Select(index => Task.Run(() =>
            {
                start.Wait();
                var user = index % 2 == 0 ? first : second;
                if (index % 2 == 0)
                    database.WriteNonLedger(user.Id, sql => { audit.Write(sql, user.Id, action, null, index.ToString(), DateTime.UtcNow); return 0; });
                else
                {
                    using var connection = new NpgsqlConnection(user.Connection);
                    connection.Open();
                    using var transaction = connection.BeginTransaction();
                    AdminAuditRepository.WriteRaw(connection, transaction, "SHA256", user.Id, action, null, index.ToString(), DateTime.UtcNow);
                    transaction.Commit();
                }
            })).ToArray();
            start.Set();
            await Task.WhenAll(tasks);
            Assert.Equal(9L, first.Query("SELECT count(*) FROM admin_audit WHERE action=$1", action));
            var verification = audit.Verify();
            Assert.True(verification["status"]!.GetValue<string>() == "ok", verification.ToJsonString());
        }
        finally
        {
            first.Execute("DROP TRIGGER branch_repair_audit_delay ON admin_audit");
            first.Execute("DROP FUNCTION branch_repair_audit_delay()");
            first.Execute("DELETE FROM admin_audit WHERE action=$1", action);
        }
    }
}
