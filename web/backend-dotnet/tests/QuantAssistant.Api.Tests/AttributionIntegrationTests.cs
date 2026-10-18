using Npgsql;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class AttributionIntegrationTests
{
    private readonly string _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION") ?? "";

    [PgFact]
    public void AttributionRepository_CanCreateAndReadReport()
    {
        if (string.IsNullOrEmpty(_connection)) return;

        var repo = new AttributionRepository(_connection);
        var userId = Guid.NewGuid().ToString("N");
        var reportId = Guid.NewGuid().ToString("N");
        
        SeedUser(userId);

        try
        {
            repo.CreateReport(new AttributionReport
            {
                Id = reportId,
                UserId = userId,
                PortfolioSymbol = "P1",
                BenchmarkSymbol = "000300",
                StartDate = new DateOnly(2026, 1, 1),
                EndDate = new DateOnly(2026, 1, 31),
                Status = "draft"
            });

            var details = new List<AttributionDetail>
            {
                new() { ReportId = reportId, SectorName = "Tech", PortfolioWeight = 0.5m, BenchmarkWeight = 0.4m, PortfolioReturn = 0.1m, BenchmarkReturn = 0.08m },
                new() { ReportId = reportId, SectorName = "Finance", PortfolioWeight = 0.5m, BenchmarkWeight = 0.6m, PortfolioReturn = 0.05m, BenchmarkReturn = 0.04m }
            };

            BrinsonModel.CalculateEffects(details);
            repo.UpsertDetails(details);
            
            repo.UpdateReportStatus(
                reportId, 
                "completed", 
                details.Sum(d => d.PortfolioWeight * d.PortfolioReturn) - details.Sum(d => d.BenchmarkWeight * d.BenchmarkReturn),
                details.Sum(d => d.Allocation),
                details.Sum(d => d.Selection),
                details.Sum(d => d.Interaction)
            );

            var dbReport = repo.GetReport(userId, reportId);
            Assert.NotNull(dbReport);
            Assert.Equal("completed", dbReport.Status);
            Assert.Equal(0.019m, dbReport.TotalActiveReturn); // (0.5*0.1 + 0.5*0.05) - (0.4*0.08 + 0.6*0.04) = 0.075 - 0.056 = 0.019

            var dbDetails = repo.GetDetails(reportId);
            Assert.Equal(2, dbDetails.Count);
            Assert.Equal(0.0024m, dbDetails.Single(d => d.SectorName == "Tech").Allocation);
        }
        finally
        {
            using var conn = new NpgsqlConnection(_connection);
            conn.Open();
            foreach (var table in new[] { "attribution_reports", "users" })
            {
                using var cmd = conn.CreateCommand();
                cmd.CommandText = $"DELETE FROM {table} WHERE user_id = $1"; 
                if (table == "users") cmd.CommandText = $"DELETE FROM {table} WHERE id = $1";
                cmd.Parameters.AddWithValue(userId);
                cmd.ExecuteNonQuery();
            }
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
}
