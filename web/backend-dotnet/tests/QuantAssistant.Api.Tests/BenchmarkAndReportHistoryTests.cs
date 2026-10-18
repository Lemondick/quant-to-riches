using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class BenchmarkAndReportHistoryTests
{
    [PgFact]
    public async Task BenchmarkEdits_NormalizeSymbolsAndIsolateDefaults_AndInvalidInputDoesNotWrite()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        using var second = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        PgTestUser.AttachSession(second, user.Connection, other.Id);
        async Task<JsonElement> Save(HttpClient target, string id, Dictionary<string, double> components, bool isDefault = true)
        {
            var response = await target.PostAsJsonAsync("/api/v1/research/benchmarks", new { benchmark_id = id, name = " 合成基准 ", components, is_default = isDefault });
            Assert.Equal(HttpStatusCode.OK, response.StatusCode);
            return await response.Content.ReadFromJsonAsync<JsonElement>();
        }
        try
        {
            await Save(second, "shared", new() { ["CASH"] = 1 });
            var first = await Save(client, "shared", new() { [" 600000.sh "] = 0.6, [" cash.cny "] = 0.4 });
            Assert.Equal("合成基准", first.GetProperty("name").GetString());
            Assert.Equal(0.6, first.GetProperty("components").GetProperty("600000.SH").GetDouble());
            Assert.Equal(0.4, first.GetProperty("components").GetProperty("CASH").GetDouble());
            await Save(client, "next", new() { ["CASH"] = 1 });
            Assert.Equal(1L, user.Query("SELECT count(*) FROM benchmark_configs WHERE user_id=$1 AND is_default", user.Id));
            Assert.Equal(1L, other.Query("SELECT count(*) FROM benchmark_configs WHERE user_id=$1 AND is_default", other.Id));
            await Save(client, "shared", new() { ["600000.SH"] = 1 }, false);
            Assert.Equal(2L, user.Query("SELECT count(*) FROM benchmark_configs WHERE user_id=$1", user.Id));
            foreach (var components in new[]
            {
                new Dictionary<string, double> { ["600000.SH"] = 0.5, [" 600000.sh "] = 0.5 },
                new Dictionary<string, double> { ["CASH"] = 0.5, ["CASH.CNY"] = 0.5 },
                new Dictionary<string, double> { ["600 000.SH"] = 1 },
                new Dictionary<string, double> { ["CASH"] = 0 },
                new Dictionary<string, double> { ["CASH"] = 0.5 },
                new Dictionary<string, double>()
            })
                Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync("/api/v1/research/benchmarks", new { benchmark_id = "next", name = "不可覆盖", components, is_default = true })).StatusCode);
            Assert.Equal("合成基准", user.Query("SELECT name FROM benchmark_configs WHERE user_id=$1 AND benchmark_id='next'", user.Id));
            var isolated = await second.GetFromJsonAsync<JsonElement>("/api/v1/research/benchmarks");
            Assert.Equal("CASH", Assert.Single(Assert.Single(isolated.GetProperty("items").EnumerateArray()).GetProperty("components").EnumerateObject()).Name);
            // IDs accepted by the configuration API must also be accepted by automatic attribution.
            var longId = new string('x', 128);
            await Save(client, longId, new() { ["CASH"] = 1 }, false);
            var future = DateOnly.FromDateTime(DateTime.UtcNow).AddDays(2).ToString("yyyy-MM-dd");
            var automatic = await client.PostAsJsonAsync("/api/v1/attribution/reports/automatic", new { portfolio_symbol = "P1", benchmark_id = longId, start_date = "2026-01-01", end_date = future });
            Assert.Equal(HttpStatusCode.OK, automatic.StatusCode);
            Assert.Equal("blocked", (await automatic.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("status").GetString());
        }
        finally
        {
            user.Execute("DELETE FROM benchmark_configs WHERE user_id=$1", user.Id);
            other.Execute("DELETE FROM benchmark_configs WHERE user_id=$1", other.Id);
        }
    }

    [PgFact]
    public async Task ReportHistory_IsOwnedBoundedAndOrdered_AndReadbackPreservesTheSavedView()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        using var second = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        PgTestUser.AttachSession(second, user.Connection, other.Id);
        var ids = new List<string>();
        for (var index = 0; index < 3; index++)
        {
            var response = await client.PostAsJsonAsync("/api/v1/attribution/reports", new
            {
                portfolio_symbol = "合成组合" + index, benchmark_symbol = "CASH", start_date = "2026-01-01", end_date = "2026-01-31",
                details = new[] { new { sector_name = "现金", portfolio_weight = 1m, benchmark_weight = 1m, portfolio_return = 0.1m, benchmark_return = 0m } }
            });
            Assert.Equal(HttpStatusCode.Created, response.StatusCode);
            ids.Add((await response.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetString()!);
        }
        // Equal timestamps must still give deterministic, non-overlapping pages.
        user.Execute("UPDATE attribution_reports SET created_at='2026-10-07 00:00:00Z' WHERE user_id=$1", user.Id);
        ids.Sort(StringComparer.Ordinal);
        ids.Reverse();
        var first = await client.GetFromJsonAsync<JsonElement>("/api/v1/attribution/reports?limit=2&offset=0");
        var last = await client.GetFromJsonAsync<JsonElement>("/api/v1/attribution/reports?limit=2&offset=2");
        Assert.Equal(3, first.GetProperty("total").GetInt32());
        Assert.True(first.GetProperty("has_more").GetBoolean());
        Assert.False(last.GetProperty("has_more").GetBoolean());
        Assert.Equal(ids, first.GetProperty("items").EnumerateArray().Concat(last.GetProperty("items").EnumerateArray()).Select(item => item.GetProperty("id").GetString()));
        Assert.Equal(0.1m, first.GetProperty("items")[0].GetProperty("total_active_return").DecimalText());
        Assert.False(first.GetProperty("items")[0].TryGetProperty("source_json", out _));
        var before = await client.GetStringAsync("/api/v1/attribution/reports/" + ids[0]);
        await client.GetAsync("/api/v1/attribution/reports");
        Assert.Equal(before, await client.GetStringAsync("/api/v1/attribution/reports/" + ids[0]));
        Assert.Empty((await second.GetFromJsonAsync<JsonElement>("/api/v1/attribution/reports")).GetProperty("items").EnumerateArray());
        Assert.Equal(HttpStatusCode.NotFound, (await second.GetAsync("/api/v1/attribution/reports/" + ids[0])).StatusCode);
        foreach (var query in new[] { "limit=0", "limit=101", "offset=-1", "offset=100001" })
            Assert.Equal(HttpStatusCode.BadRequest, (await client.GetAsync("/api/v1/attribution/reports?" + query)).StatusCode);
    }
}
