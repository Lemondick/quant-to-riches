using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;
using Microsoft.Extensions.Caching.Memory;

namespace QuantAssistant.Api.Tests;

public class PeerApiIntegrationTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly string _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION") ?? "";

    public PeerApiIntegrationTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        if (!string.IsNullOrEmpty(_connection))
        {
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", _connection);
        }
    }

    [PgFact]
    public async Task PeerApi_ReturnsCorrectRankings()
    {
        if (string.IsNullOrEmpty(_connection)) return;

        var repo = new PeerRepository(_connection, new MemoryCache(new MemoryCacheOptions()));
        var groupId = Guid.NewGuid().ToString("N");
        var date = new DateOnly(2026, 10, 5);

        try
        {
            repo.CreateGroup(new PeerGroup { Id = groupId, GroupName = "Test Group" });
            var rankings = new List<PeerRanking>
            {
                new() { Symbol = "F1", GroupId = groupId, DataDate = date, Return1M = 0.1m, Rank1M = 1, Percentile1M = 100m }
            };
            repo.UpsertRankings(rankings);

            using var user = new PgTestUser();
            var client = _factory.WithWebHostBuilder(b => { }).CreateClient();
            PgTestUser.AttachSession(client, _connection, user.Id);

            // Test 1: Get Group Rankings
            var groupRes = await client.GetAsync($"/api/v1/peers/groups/{groupId}/rankings?date=2026-10-05");
            groupRes.EnsureSuccessStatusCode();
            var groupJson = await groupRes.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal("2026-10-05", groupJson.GetProperty("data_date").GetString());
            var results = groupJson.GetProperty("results");
            Assert.Equal(1, results.GetArrayLength());
            Assert.Equal("F1", results[0].GetProperty("symbol").GetString());

            // Test 2: Get Fund Ranking
            var fundRes = await client.GetAsync($"/api/v1/peers/funds/F1/ranking?group_id={groupId}&date=2026-10-05");
            fundRes.EnsureSuccessStatusCode();
            var fundJson = await fundRes.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal("F1", fundJson.GetProperty("symbol").GetString());
            Assert.Equal(100m, fundJson.GetProperty("percentile_1m").DecimalText());
        }
        finally
        {
            using var conn = new NpgsqlConnection(_connection);
            conn.Open();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "DELETE FROM peer_groups WHERE id = $1";
            cmd.Parameters.AddWithValue(groupId);
            cmd.ExecuteNonQuery();
        }
    }
}
