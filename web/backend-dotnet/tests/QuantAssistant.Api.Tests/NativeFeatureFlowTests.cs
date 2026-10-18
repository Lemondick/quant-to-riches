using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class NativeFeatureFlowTests
{
    [PgFact]
    public async Task Explanation_MissingOrOtherAccountRun_ReturnsNotFoundBeforeWorkerExecution()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        using (var connection = new NpgsqlConnection(user.Connection))
        {
            connection.Open();
            using var command = new NpgsqlCommand("INSERT INTO strategy_runs(user_id,run_id,as_of_date,generated_at,strategy_version,data_status,readiness,status) VALUES($1,'synthetic-private-run',current_date,now(),'synthetic','blocked','research_only','completed')", connection);
            command.Parameters.AddWithValue(other.Id);
            command.ExecuteNonQuery();
        }
        try
        {
            using var factory = Factory(user.Connection);
            using var client = factory.CreateClient();
            PgTestUser.AttachSession(client, user.Connection, user.Id);
            foreach (var id in new[] { "synthetic-private-run", "synthetic-missing-run" })
            {
                var response = await client.PostAsJsonAsync("/api/v1/analysis/explain", new { decision_run_id = id });
                Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
                var error = await response.Content.ReadFromJsonAsync<JsonElement>();
                Assert.Equal("decision_run_not_found", error.GetProperty("error_code").GetString());
            }
        }
        finally { other.Execute("DELETE FROM strategy_runs WHERE user_id=$1", other.Id); }
    }

    private static WebApplicationFactory<Program> Factory(string connection)
    {
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", connection);
        return new WebApplicationFactory<Program>();
    }

    private static async Task<JsonElement> Json(HttpResponseMessage response)
    {
        Assert.True(response.IsSuccessStatusCode, $"{response.RequestMessage?.RequestUri}: {response.StatusCode} {await response.Content.ReadAsStringAsync()}");
        return await response.Content.ReadFromJsonAsync<JsonElement>();
    }

    [PgFact]
    public async Task AuthenticationReadTraffic_DoesNotConsumeTheLoginRateLimit()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var anonymous = factory.CreateClient();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        for (var index = 0; index < 25; index++)
        {
            Assert.Equal(HttpStatusCode.OK, (await anonymous.GetAsync("/api/v1/auth/status")).StatusCode);
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/api/v1/auth/session")).StatusCode);
        }
        for (var index = 0; index < 20; index++)
            Assert.Equal(HttpStatusCode.Unauthorized, (await anonymous.PostAsJsonAsync("/api/v1/auth/login", new { username = "!!", password = "SyntheticWrongPassword" })).StatusCode);
        var rejected = false;
        for (var index = 0; index < 5; index++)
            rejected |= (await anonymous.PostAsJsonAsync("/api/v1/auth/login", new { username = "!!", password = "SyntheticWrongPassword" })).StatusCode == HttpStatusCode.TooManyRequests;
        Assert.True(rejected, "A bounded burst of invalid logins must trigger the configured token bucket.");
        Assert.Equal(HttpStatusCode.OK, (await anonymous.GetAsync("/api/v1/auth/status")).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/api/v1/auth/sessions")).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await client.PostAsJsonAsync("/api/v1/auth/logout", new { })).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/v1/auth/session")).StatusCode);
    }

    [PgFact]
    public async Task SuccessfulLogin_RecordsTimeAndActivityWithoutCountingFailures()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateClient();
        var repository = new AccountRepository(user.Connection);
        Assert.Null(repository.FindById(user.Id)!.LastLoginAt);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/api/v1/auth/login", new { username = user.Username, password = "SyntheticWrongPassword" })).StatusCode);
        Assert.Null(repository.FindById(user.Id)!.LastLoginAt);
        var response = await Json(await client.PostAsJsonAsync("/api/v1/auth/login", new { username = user.Username, password = PgTestUser.Password }));
        Assert.NotEqual(JsonValueKind.Null, response.GetProperty("user").GetProperty("last_login_at").ValueKind);
        var timestamp = repository.FindById(user.Id)!.LastLoginAt;
        Assert.NotNull(timestamp);
        Assert.Equal(1L, user.Query("SELECT count(*) FROM account_activity WHERE user_id=$1 AND action='login'", user.Id));
        Assert.Equal(timestamp, user.Query("SELECT created_at FROM account_activity WHERE user_id=$1 AND action='login'", user.Id));
    }

    [PgFact]
    public void FailedSessionCreation_DoesNotAdvanceLoginTimeOrLeaveActivity()
    {
        using var user = new PgTestUser();
        var repository = new AccountRepository(user.Connection);
        var now = new DateTime(2026, 10, 7, 0, 0, 0, DateTimeKind.Utc);
        repository.CreateSession(user.Id, Guid.NewGuid().ToString("N"), user.Id, "test", "test", now, now.AddHours(1));
        var failure = Assert.Throws<PostgresException>(() => repository.CreateSession(user.Id, Guid.NewGuid().ToString("N"), user.Id, "test", "test", now.AddMinutes(1), now.AddHours(2)));
        Assert.Equal(PostgresErrorCodes.UniqueViolation, failure.SqlState);
        Assert.Equal(now, repository.FindById(user.Id)!.LastLoginAt);
        Assert.Equal(1L, user.Query("SELECT count(*) FROM account_activity WHERE user_id=$1 AND action='login'", user.Id));
    }

    [PgFact]
    public async Task InitializedLoginPageAndAdminRegistration_LoadExistingAccounts()
    {
        using var admin = new PgTestUser();
        using var user = new PgTestUser();
        admin.Execute("UPDATE users SET role='admin' WHERE id=$1", admin.Id);
        user.Execute("UPDATE users SET last_login_at=now() WHERE id=$1", user.Id);
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        user.Execute("INSERT INTO usage_daily(user_id,day,category,requests,errors,duration_ms,last_activity_at) VALUES($1,$2,'portfolio',2,1,50,now())", user.Id, today.ToString("yyyy-MM-dd"));
        user.Execute("INSERT INTO usage_daily(user_id,day,category,requests,errors,duration_ms,last_activity_at) VALUES($1,$2,'portfolio',99,99,50,now())", user.Id, today.AddDays(-7).ToString("yyyy-MM-dd"));
        var repository = new AccountRepository(admin.Connection);
        var previous = repository.RegistrationEnabled();
        using var factory = Factory(admin.Connection);
        using var anonymous = factory.CreateClient();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, admin.Connection, admin.Id);
        try
        {
            var status = await Json(await anonymous.GetAsync("/api/v1/auth/status"));
            Assert.True(status.GetProperty("initialized").GetBoolean());
            Assert.True(status.GetProperty("registered").GetInt32() >= 2);
            var enabled = await Json(await client.PostAsJsonAsync("/api/v1/admin/registration", new { enabled = true }));
            Assert.True(enabled.GetProperty("registration_open").GetBoolean());
            var users = await Json(await client.GetAsync("/api/v1/admin/users?search=" + user.Username));
            var row = Assert.Single(users.GetProperty("items").EnumerateArray());
            Assert.Equal(user.Id, row.GetProperty("id").GetString());
            Assert.NotEqual(JsonValueKind.Null, row.GetProperty("created_at").ValueKind);
            Assert.NotEqual(JsonValueKind.Null, row.GetProperty("last_login_at").ValueKind);
            Assert.Equal(2, row.GetProperty("requests_7d").GetInt32());
            Assert.Equal(1, row.GetProperty("errors_7d").GetInt32());
            var filtered = Assert.Single(repository.ListUsers(user.Username));
            Assert.Equal(user.Id, filtered.Id);
            Assert.NotNull(filtered.CreatedAt);
            Assert.NotNull(filtered.LastLoginAt);
            foreach (var section in new[] { "holdings", "ledger", "watchlist", "reports", "tasks" })
            {
                var detail = await Json(await client.GetAsync($"/api/v1/admin/users/{user.Id}/details?section={section}"));
                Assert.Equal(user.Id, detail.GetProperty("user").GetProperty("id").GetString());
                Assert.Empty(detail.GetProperty("items").EnumerateArray());
            }
            await Json(await client.GetAsync($"/api/v1/admin/users/{user.Id}/usage"));
            await Json(await client.GetAsync($"/api/v1/admin/users/{user.Id}/timeline"));
        }
        finally
        {
            admin.Execute("UPDATE site_settings SET value=$1 WHERE key='registration_enabled'", previous ? "1" : "0");
            user.Execute("DELETE FROM usage_daily WHERE user_id=$1", user.Id);
        }
    }

    [PgFact]
    public async Task PreferencesCashPlansAndCosts_RoundTripWithoutCrossAccountLeaks()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateClient();
        using var second = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        PgTestUser.AttachSession(second, other.Connection, other.Id);
        try
        {
            var watchlist = new { items = new[] { new { symbol = "510300", name = "Synthetic fund", asset_type = "fund" } } };
            var saved = await Json(await client.PutAsJsonAsync("/api/v1/preferences/watchlist", watchlist));
            Assert.Equal("510300", Assert.Single(saved.GetProperty("watchlist").EnumerateArray()).GetProperty("symbol").GetString());
            var unchanged = await Json(await client.PutAsJsonAsync("/api/v1/preferences/watchlist", watchlist));
            Assert.Equal(saved.GetProperty("revision").GetInt64(), unchanged.GetProperty("revision").GetInt64());
            await Json(await client.PostAsJsonAsync("/api/v1/preferences/news/read", new { ids = new[] { "synthetic-news", "synthetic-news" } }));
            var preferences = await Json(await client.GetAsync("/api/v1/preferences"));
            Assert.Single(preferences.GetProperty("read_news_ids").EnumerateArray());
            var isolated = await Json(await second.GetAsync("/api/v1/preferences"));
            Assert.Empty(isolated.GetProperty("watchlist").EnumerateArray());
            Assert.Empty(isolated.GetProperty("read_news_ids").EnumerateArray());

            var plan = await Json(await client.PostAsJsonAsync("/api/v1/planning/cash-flows", new
            {
                name = "Synthetic monthly deposit", direction = "deposit", amount = 1500.00000001m,
                frequency = "monthly", start_date = "2026-01-31", end_date = "2026-03-31",
            }));
            Assert.Equal(1500.00000001m, plan.GetProperty("amount").DecimalText());
            Assert.Equal(new[] { "2026-01-31", "2026-02-28", "2026-03-31" }, plan.GetProperty("next_dates").EnumerateArray().Select(item => item.GetString()).ToArray());
            var due = await Json(await client.GetAsync("/api/v1/planning/cash-flows/due?as_of=2026-02-28"));
            Assert.Equal(plan.GetProperty("plan_id").GetString(), Assert.Single(due.GetProperty("items").EnumerateArray()).GetProperty("plan_id").GetString());
            Assert.Empty((await Json(await second.GetAsync("/api/v1/planning/cash-flows"))).GetProperty("items").EnumerateArray());

            var settings = new { revision = 0, currency = "CNY", daily_budget = 100.00000001m, prices = new { } };
            var costs = await Json(await client.PutAsJsonAsync("/api/v1/runtime/costs", settings));
            Assert.Equal(1, costs.GetProperty("settings").GetProperty("revision").GetInt32());
            Assert.Equal(100.00000001m, costs.GetProperty("settings").GetProperty("daily_budget").DecimalText());
            Assert.Equal(HttpStatusCode.Conflict, (await client.PutAsJsonAsync("/api/v1/runtime/costs", settings)).StatusCode);
            Assert.Equal(0, (await Json(await second.GetAsync("/api/v1/runtime/costs"))).GetProperty("settings").GetProperty("revision").GetInt32());

            await Json(await client.PostAsJsonAsync("/api/v1/operations/watchdog/heartbeat", new { job_name = "synthetic-job", status = "completed" }));
            var heartbeat = Assert.Single((await Json(await client.GetAsync("/api/v1/operations/watchdog"))).GetProperty("items").EnumerateArray());
            Assert.True(heartbeat.GetProperty("healthy").GetBoolean());
            Assert.Empty((await Json(await second.GetAsync("/api/v1/operations/watchdog"))).GetProperty("items").EnumerateArray());
        }
        finally
        {
            foreach (var table in new[] { "user_documents", "cash_flow_plans", "job_heartbeats" })
                user.Execute($"DELETE FROM {table} WHERE user_id=$1", user.Id);
        }
    }

    [PgFact]
    public async Task NativePageModules_ReadEmptyAccountWithoutServerErrors()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        foreach (var path in new[]
        {
            "/api/v1/portfolio", "/api/v1/portfolio/history", "/api/v1/portfolio/photo-holdings",
            "/api/v1/ledger", "/api/v1/ledger/actions", "/api/v1/ledger/corrections",
            "/api/v1/ledger/reconciliation/accounts", "/api/v1/ledger/reconciliation/history",
            "/api/v1/ledger/replay/history", "/api/v1/dca/plans", "/api/v1/preferences",
            "/api/v1/planning/cash-flows", "/api/v1/planning/cash-flows/due", "/api/v1/runtime/costs",
            "/api/v1/operations/watchdog", "/api/v1/operations/trends", "/api/v1/onboarding",
            "/api/v1/advisor/reports", "/api/v1/advisor/schedule", "/api/v1/advisor/notifications",
            "/api/v1/funds/510300/fee-evidence", "/api/v1/funds/510300/holding-lots",
            "/api/v1/auth/session", "/api/v1/auth/sessions",
        })
        {
            var body = await Json(await client.GetAsync(path));
            Assert.Contains(body.ValueKind, new[] { JsonValueKind.Object, JsonValueKind.Array });
        }
    }
}
