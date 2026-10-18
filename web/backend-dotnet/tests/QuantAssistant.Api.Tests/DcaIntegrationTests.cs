using Npgsql;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class DcaIntegrationTests
{
    private readonly string _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION") ?? "";

    [PgFact]
    public void DcaRepository_CanCreateAndListAndComplete()
    {
        if (string.IsNullOrEmpty(_connection)) return;

        var userId = Guid.NewGuid().ToString("N");
        var planId = Guid.NewGuid().ToString("N");

        SeedUser(userId);

        var repo = new DcaRepository(_connection);
        
        try
        {
            repo.CreatePlan(new DcaPlan
            {
                Id = planId,
                UserId = userId,
                Symbol = "000001",
                BenchmarkSymbol = "000300",
                BaseAmount = 1000m,
                MaDays = 250,
                MinMultiplier = 0.6m,
                MaxMultiplier = 2.5m,
                Status = "active"
            });

            var active = repo.ListActivePlans(userId);
            Assert.Single(active);
            Assert.Equal(planId, active[0].Id);
            Assert.Equal("000300", active[0].BenchmarkSymbol);
            Assert.Equal(0.6m, active[0].MinMultiplier);

            repo.UpdatePlanStatus(userId, planId, "finished");
            
            var activeAfter = repo.ListActivePlans(userId);
            Assert.Empty(activeAfter);
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
        foreach (var table in new[] { "dca_plans", "users" })
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = $"DELETE FROM {table} WHERE user_id = $1"; // except users where id=$1
            if (table == "users") cmd.CommandText = $"DELETE FROM {table} WHERE id = $1";
            cmd.Parameters.AddWithValue(userId);
            cmd.ExecuteNonQuery();
        }
    }
}
