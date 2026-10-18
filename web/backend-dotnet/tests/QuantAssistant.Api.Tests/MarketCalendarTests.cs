using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Core.Market;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class MarketCalendarTests
{
    [Fact]
    public void BuiltinCalendarDoesNotImputeUnknownMarketsOrYears()
    {
        var calendars = MarketCalendars.Builtin;
        Assert.False(calendars.TradingDay("CN", new(2026, 10, 5)));
        Assert.True(calendars.TradingDay("CN", new(2026, 10, 19)));
        Assert.Null(calendars.TradingDay("HK", new(2026, 10, 5)));
        Assert.Null(calendars.TradingDay("CN", new(2027, 1, 4)));
    }

    [Fact]
    public void ImmutableSnapshotKeepsMarketsSeparateAndRejectsConflictingVersions()
    {
        var payload = JsonNode.Parse("""{"format_version":1,"calendars":[{"market":"CN","year":2026,"source":"https://www.sse.com.cn/test","revision":"cn-v1","holidays":["2026-10-19"]},{"market":"HK","year":2026,"source":"https://www.hkex.com.hk/test","revision":"hk-v1","holidays":[]}]}""")!;
        var calendars = MarketCalendars.FromJson(payload);
        Assert.False(calendars.TradingDay("CN", new(2026, 10, 19)));
        Assert.True(calendars.TradingDay("HK", new(2026, 10, 19)));
        Assert.Null(calendars.TradingDay("US", new(2026, 10, 19)));
        payload["calendars"]!.AsArray().Add(payload["calendars"]![0]!.DeepClone());
        Assert.Null(MarketCalendars.FromJson(payload).TradingDay("CN", new(2026, 10, 19)));
        Assert.False(calendars.TradingDay("CN", new(2026, 10, 19)));
        Assert.False(MarketCalendars.FromJson(calendars.ToJson()).TradingDay("CN", new(2026, 10, 19)));
    }

    private const string WorkerSecret = "synthetic-calendar-worker-secret-for-tests";
    private static WebApplicationFactory<Program> Factory(PgTestUser user) => new WebApplicationFactory<Program>()
        .WithWebHostBuilder(builder => builder.UseSetting("QUANT_PG_CONNECTION", user.Connection).UseSetting("QUANT_WORKER_SECRET", WorkerSecret));

    private static async Task<JsonElement> Json(HttpResponseMessage response)
    {
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());
        return await response.Content.ReadFromJsonAsync<JsonElement>();
    }

    [PgFact]
    public async Task UnconfiguredHongKongCalendarDoesNotFallBackToMainlandHolidays()
    {
        using var admin = new PgTestUser();
        admin.Execute("UPDATE users SET role='admin' WHERE id=$1", admin.Id);
        const string key = "calendar:HK:2026";
        var old = admin.Query("SELECT value FROM site_settings WHERE key=$1", key) as string;
        admin.Execute("DELETE FROM site_settings WHERE key=$1", key);
        try
        {
            using var factory = Factory(admin);
            using var client = factory.CreateClient();
            PgTestUser.AttachSession(client, admin.Connection, admin.Id);
            var cn = await Json(await client.GetAsync("/api/v1/admin/calendar?year=2026&market=CN"));
            var hk = await Json(await client.GetAsync("/api/v1/admin/calendar?year=2026&market=HK"));
            Assert.True(cn.GetProperty("configured").GetBoolean());
            Assert.False(hk.GetProperty("configured").GetBoolean());
            Assert.Equal("HK", hk.GetProperty("market").GetString());
            var saved = await Json(await client.PutAsJsonAsync("/api/v1/admin/calendar", new
            {
                year = 2026, market = "HK", source = "https://www.hkex.com.hk/synthetic-test", holidays = new[] { "2026-10-19" },
                expected_revision = hk.GetProperty("revision").GetString(),
            }));
            Assert.Equal("HK", saved.GetProperty("market").GetString());
            Assert.NotEqual(cn.GetProperty("revision").GetString(), saved.GetProperty("revision").GetString());
            var cnAfter = await Json(await client.GetAsync("/api/v1/admin/calendar?year=2026&market=CN"));
            Assert.Equal(cn.GetProperty("revision").GetString(), cnAfter.GetProperty("revision").GetString());
        }
        finally
        {
            admin.Execute("DELETE FROM admin_audit WHERE actor_id=$1", admin.Id);
            admin.Execute("DELETE FROM site_settings WHERE key=$1", key);
            if (old is not null) admin.Execute("INSERT INTO site_settings(key,value) VALUES($1,$2)", key, old);
        }
    }

    [PgFact]
    public async Task AuthorizedClaimReceivesCalendarRevisionAndOverridesUntrustedTaskCalendar()
    {
        using var admin = new PgTestUser();
        admin.Execute("UPDATE users SET role='admin' WHERE id=$1", admin.Id);
        var year = DateTime.UtcNow.AddHours(8).Year;
        var key = "calendar:" + year;
        var old = admin.Query("SELECT value FROM site_settings WHERE key=$1", key) as string;
        try
        {
            using var factory = Factory(admin);
            using var client = factory.CreateClient();
            PgTestUser.AttachSession(client, admin.Connection, admin.Id);
            await factory.Services.GetRequiredService<TradingCalendarRepository>().SnapshotAsync(year);
            var prior = await Json(await client.GetAsync("/api/v1/admin/calendar?year=" + year));
            var closure = new DateOnly(year, 10, 19).ToString("yyyy-MM-dd");
            var saved = await Json(await client.PutAsJsonAsync("/api/v1/admin/calendar", new
            {
                year, source = "https://www.sse.com.cn/synthetic-test", holidays = new[] { closure },
                expected_revision = prior.GetProperty("revision").GetString(),
            }));
            var jobs = new ComputeJobRepository(admin.Connection);
            var id = jobs.Enqueue(admin.Id, "market.quotes", "{\"stocks\":[],\"indices\":[],\"_calendars\":{\"format_version\":999}}", DateTime.UtcNow.AddMinutes(1));
            var hash = admin.Query("SELECT input_hash FROM compute_jobs WHERE user_id=$1 AND job_id=$2", admin.Id, id);
            using var worker = factory.CreateClient();
            worker.DefaultRequestHeaders.Add("X-Quant-Worker-Secret", WorkerSecret);
            var job = await Json(await worker.PostAsJsonAsync("/internal/v1/worker/claim", new { worker_id = "synthetic-calendar-worker", capabilities = new[] { "market.quotes" } }));
            var input = job.GetProperty("input");
            var snapshot = input.GetProperty("_calendars");
            Assert.Equal(1, snapshot.GetProperty("format_version").GetInt32());
            var calendar = Assert.Single(snapshot.GetProperty("calendars").EnumerateArray(), item => item.GetProperty("market").GetString() == "CN" && item.GetProperty("year").GetInt32() == year);
            Assert.Equal(saved.GetProperty("revision").GetString(), calendar.GetProperty("revision").GetString());
            Assert.Contains(calendar.GetProperty("holidays").EnumerateArray(), item => item.GetString() == closure);
            Assert.DoesNotContain("Host=", input.GetRawText(), StringComparison.OrdinalIgnoreCase);
            Assert.Equal(hash, admin.Query("SELECT input_hash FROM compute_jobs WHERE user_id=$1 AND job_id=$2", admin.Id, id));
        }
        finally
        {
            admin.Execute("DELETE FROM admin_audit WHERE actor_id=$1", admin.Id);
            admin.Execute("DELETE FROM site_settings WHERE key=$1", key);
            if (old is not null) admin.Execute("INSERT INTO site_settings(key,value) VALUES($1,$2)", key, old);
        }
    }
}
