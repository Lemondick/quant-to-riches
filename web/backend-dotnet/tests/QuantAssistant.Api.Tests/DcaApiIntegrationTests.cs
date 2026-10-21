using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class DcaApiIntegrationTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly string _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION") ?? "";

    public DcaApiIntegrationTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        if (!string.IsNullOrEmpty(_connection))
        {
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", _connection);
        }
    }

    [PgFact]
    public async Task DcaApi_CanCreateListAndCalculate()
    {
        if (string.IsNullOrEmpty(_connection)) return;

        var userId = Guid.NewGuid().ToString("N");
        SeedUser(userId);

        var client = _factory.WithWebHostBuilder(b => { }).CreateTestClient();
        PgTestUser.AttachSession(client, _connection, userId);

        try
        {
            // 1. Create
            var createRes = await client.PostAsJsonAsync("/api/v1/dca/plans", new
            {
                symbol = "000001",
                benchmark_symbol = "000300",
                base_amount = 1000m,
                ma_days = 250,
                min_multiplier = 0.5m,
                max_multiplier = 2.0m
            });
            createRes.EnsureSuccessStatusCode();

            // 2. List
            var listRes = await client.GetAsync("/api/v1/dca/plans");
            listRes.EnsureSuccessStatusCode();
            var jsonArray = await listRes.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal(1, jsonArray.GetArrayLength());
            var planId = jsonArray[0].GetProperty("id").GetString();
            Assert.NotNull(planId);

            // 3. Calculate (simulate deviation +10% -> half way to min => 1.0 - 0.5 * 0.5 = 0.75 multiplier)
            // base_amount = 1000, final_amount = 750
            var calcRes = await client.PostAsJsonAsync($"/api/v1/dca/plans/{planId}/calculate", new
            {
                current_price = 110m,
                moving_average_price = 100m,
                deviation_threshold = 0.20m
            });
            calcRes.EnsureSuccessStatusCode();
            
            var calcJson = await calcRes.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal(0.75m, calcJson.GetProperty("multiplier").DecimalText());
            Assert.Equal(750m, calcJson.GetProperty("final_amount").DecimalText());
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
        foreach (var table in new[] { "dca_plans", "sessions", "users" })
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = $"DELETE FROM {table} WHERE user_id = $1"; 
            if (table == "users") cmd.CommandText = $"DELETE FROM {table} WHERE id = $1";
            cmd.Parameters.AddWithValue(userId);
            cmd.ExecuteNonQuery();
        }
    }
}
