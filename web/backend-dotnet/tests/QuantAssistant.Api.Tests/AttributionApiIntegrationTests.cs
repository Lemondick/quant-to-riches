using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using QuantAssistant.Core.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class AttributionApiIntegrationTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly string _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION") ?? "";

    public AttributionApiIntegrationTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        if (!string.IsNullOrEmpty(_connection))
        {
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", _connection);
        }
    }

    [PgFact]
    public async Task AttributionApi_CanCreateAndCalculateAndGet()
    {
        if (string.IsNullOrEmpty(_connection)) return;

        var userId = Guid.NewGuid().ToString("N");
        SeedUser(userId);

        var client = _factory.WithWebHostBuilder(b => { }).CreateTestClient();
        PgTestUser.AttachSession(client, _connection, userId);

        try
        {
            // 1. Create Report
            var createRes = await client.PostAsJsonAsync("/api/v1/attribution/reports", new
            {
                portfolio_symbol = "P1",
                benchmark_symbol = "000300",
                start_date = "2026-01-01",
                end_date = "2026-01-31"
            });
            createRes.EnsureSuccessStatusCode();
            var createJson = await createRes.Content.ReadFromJsonAsync<JsonElement>();
            var reportId = createJson.GetProperty("id").GetString();
            Assert.NotNull(reportId);

            // 2. Calculate Effects
            var calcRes = await client.PostAsJsonAsync($"/api/v1/attribution/reports/{reportId}/calculate", new
            {
                details = new[]
                {
                    new { sector_name = "Tech", portfolio_weight = 0.5m, benchmark_weight = 0.4m, portfolio_return = 0.1m, benchmark_return = 0.08m },
                    new { sector_name = "Finance", portfolio_weight = 0.5m, benchmark_weight = 0.6m, portfolio_return = 0.05m, benchmark_return = 0.04m }
                }
            });
            calcRes.EnsureSuccessStatusCode();
            var calcJson = await calcRes.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal(0.019m, calcJson.GetProperty("total_active_return").DecimalText());
            Assert.Equal(0.0040m, calcJson.GetProperty("allocation_effect").DecimalText()); // 0.0024 + 0.0016
            Assert.Equal(0.0140m, calcJson.GetProperty("selection_effect").DecimalText()); // 0.0080 + 0.0060

            // 3. Get Report
            var getRes = await client.GetAsync($"/api/v1/attribution/reports/{reportId}");
            getRes.EnsureSuccessStatusCode();
            var getJson = await getRes.Content.ReadFromJsonAsync<JsonElement>();
            
            var dbReport = getJson.GetProperty("report");
            Assert.Equal("completed", dbReport.GetProperty("status").GetString());
            
            var dbDetails = getJson.GetProperty("details");
            Assert.Equal(2, dbDetails.GetArrayLength());
        }
        finally
        {
            Cleanup(userId);
        }
    }

    private void SeedUser(string userId)
    {
        using var conn = new NpgsqlConnection(_connection);
        conn.Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO users (id, username, password_hash, role, status, created_at) VALUES ($1,$2,'x','user','active',now())";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue($"u_{userId[..8]}");
        cmd.ExecuteNonQuery();
    }

    private void Cleanup(string userId)
    {
        using var conn = new NpgsqlConnection(_connection);
        conn.Open();
        foreach (var table in new[] { "attribution_reports", "sessions", "users" })
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = $"DELETE FROM {table} WHERE user_id = $1"; 
            if (table == "users") cmd.CommandText = $"DELETE FROM {table} WHERE id = $1";
            cmd.Parameters.AddWithValue(userId);
            cmd.ExecuteNonQuery();
        }
    }
}
