using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using QuantAssistant.Core.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class ScreenerIntegrationTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly string? _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");

    public ScreenerIntegrationTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        if (_connection != null)
        {
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", _connection);
        }
    }

    [PgFact]
    public async Task ScreenerApi_ReturnsWeightedScores()
    {
        if (_connection is null) return;

        var userId = Guid.NewGuid().ToString("N");
        Cleanup(userId); // Ensure clean state
        SeedUser(userId);
        
        using var conn = new NpgsqlConnection(_connection);
        conn.Open();

        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = "INSERT INTO factor_definitions (user_id, factor_code, factor_name, category, direction) VALUES ($1, 'PE', 'PE', 'value', -1), ($1, 'ROE', 'ROE', 'quality', 1)";
            cmd.Parameters.AddWithValue(userId);
            cmd.ExecuteNonQuery();
        }

        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = """
                INSERT INTO factor_exposures (user_id, symbol, factor_code, data_date, z_score) VALUES 
                ($1, '000001', 'PE', '2026-10-05', 1.0),
                ($1, '000002', 'PE', '2026-10-05', 0.5),
                ($1, '000001', 'ROE', '2026-10-05', 1.2),
                ($1, '000002', 'ROE', '2026-10-05', 0.8)
                """;
            cmd.Parameters.AddWithValue(userId);
            cmd.ExecuteNonQuery();
        }

        try
        {
            var client = _factory.CreateTestClient();
            PgTestUser.AttachSession(client, _connection, userId);
            var req = new
            {
                data_date = "2026-10-05",
                weights = new Dictionary<string, decimal> { { "PE", 0.4m }, { "ROE", 0.6m } },
                top_n = 2
            };

            var res = await client.PostAsJsonAsync("/api/v1/research/screener", req);
            res.EnsureSuccessStatusCode();

            var json = await res.Content.ReadFromJsonAsync<System.Text.Json.JsonElement>();
            System.Console.WriteLine(json.ToString());
            
            Assert.Equal("2026-10-05", json.GetProperty("data_date").GetString());
            var results = json.GetProperty("results");
            Assert.Equal(2, results.GetArrayLength());

            // 000001 score: 
            // PE: 0.4 * 1.0 * (-1) = -0.4
            // ROE: 0.6 * 1.2 * 1 = 0.72
            // Total: 0.32
            
            // 000002 score:
            // PE: 0.4 * 0.5 * (-1) = -0.2
            // ROE: 0.6 * 0.8 * 1 = 0.48
            // Total: 0.28
            
            var top1 = results[0];
            Assert.Equal("000001", top1.GetProperty("symbol").GetString());
            Assert.Equal(0.32m, top1.GetProperty("total_score").DecimalText());
            Assert.Equal(-0.4m, top1.GetProperty("factors").GetProperty("PE").DecimalText());
            Assert.Equal(0.72m, top1.GetProperty("factors").GetProperty("ROE").DecimalText());

            var top2 = results[1];
            Assert.Equal("000002", top2.GetProperty("symbol").GetString());
            Assert.Equal(0.28m, top2.GetProperty("total_score").DecimalText());
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

        foreach (var table in new[] { "factor_exposures", "factor_definitions", "sessions", "users" })
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = table == "users" 
                ? $"DELETE FROM {table} WHERE id = $1" 
                : $"DELETE FROM {table} WHERE user_id = $1";
            cmd.Parameters.AddWithValue(userId);
            cmd.ExecuteNonQuery();
        }
    }
}
