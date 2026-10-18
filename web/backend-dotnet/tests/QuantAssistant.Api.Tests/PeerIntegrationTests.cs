using Npgsql;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;
using Microsoft.Extensions.Caching.Memory;

namespace QuantAssistant.Api.Tests;

public class PeerIntegrationTests
{
    private readonly string _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION") ?? "";

    [PgFact]
    public void PeerRepository_CanUpsertAndReadRankings()
    {
        if (string.IsNullOrEmpty(_connection)) return;

        var repo = new PeerRepository(_connection, new MemoryCache(new MemoryCacheOptions()));
        var groupId = Guid.NewGuid().ToString("N");
        var date = new DateOnly(2026, 10, 5);

        try
        {
            repo.CreateGroup(new PeerGroup { Id = groupId, GroupName = "Test Group", BenchmarkSymbol = "000300" });
            repo.AssignFundToGroup("F1", groupId);
            repo.AssignFundToGroup("F2", groupId);

            var rankings = new List<PeerRanking>
            {
                new() { Symbol = "F1", GroupId = groupId, DataDate = date, Return1M = 0.1m },
                new() { Symbol = "F2", GroupId = groupId, DataDate = date, Return1M = 0.05m }
            };

            PeerRules.ComputeRankings(rankings);
            repo.UpsertRankings(rankings);

            var dbRankings = repo.GetRankingsByGroup(groupId, date);
            Assert.Equal(2, dbRankings.Count);

            var dbF1 = dbRankings.Single(r => r.Symbol == "F1");
            Assert.Equal(1, dbF1.Rank1M);
            Assert.Equal(100m, dbF1.Percentile1M);

            var single = repo.GetFundRanking("F2", groupId, date);
            Assert.NotNull(single);
            Assert.Equal(2, single.Rank1M);
            Assert.Equal(0m, single.Percentile1M);
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
