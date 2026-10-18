using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using Npgsql;
using Xunit;
using Microsoft.Extensions.Caching.Memory;

namespace QuantAssistant.Api.Tests;

public sealed class FactorIntegrationTests
{
    private readonly string? _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");

    [PgFact]
    public void DefineAndListFactors_WorksCorrectly()
    {
        if (_connection is null) return;

        var userId = Guid.NewGuid().ToString("N");
        var repo = new FactorRepository(_connection, new MemoryCache(new MemoryCacheOptions()));

        try
        {
            SeedUser(userId);

            // 1. Define Factors
            repo.DefineFactor(new FactorDefinition
            {
                UserId = userId,
                FactorCode = "PE",
                FactorName = "市盈率",
                Category = FactorCategory.Value,
                Direction = -1, // 越小越好
                Description = "TTM 市盈率",
                CreatedAt = DateTimeOffset.UtcNow
            });

            repo.DefineFactor(new FactorDefinition
            {
                UserId = userId,
                FactorCode = "MOM_1M",
                FactorName = "1月动量",
                Category = FactorCategory.Momentum,
                Direction = 1, // 越大越好
                Description = null,
                CreatedAt = DateTimeOffset.UtcNow
            });

            // 2. List
            var defs = repo.ListDefinitions(userId);
            Assert.Equal(2, defs.Count);
            
            var pe = defs.Single(d => d.FactorCode == "PE");
            Assert.Equal("市盈率", pe.FactorName);
            Assert.Equal(FactorCategory.Value, pe.Category);
            Assert.Equal(-1, pe.Direction);
            
            var mom = defs.Single(d => d.FactorCode == "MOM_1M");
            Assert.Null(mom.Description);

            // 3. Upsert update existing
            repo.DefineFactor(new FactorDefinition
            {
                UserId = userId,
                FactorCode = "PE",
                FactorName = "市盈率(滚动)",
                Category = FactorCategory.Value,
                Direction = -1,
                Description = "Updated description",
                CreatedAt = DateTimeOffset.UtcNow
            });

            var updatedPe = repo.ListDefinitions(userId).Single(d => d.FactorCode == "PE");
            Assert.Equal("市盈率(滚动)", updatedPe.FactorName);
            Assert.Equal("Updated description", updatedPe.Description);
            
            // 4. Delete
            repo.DeleteFactor(userId, "MOM_1M");
            Assert.Single(repo.ListDefinitions(userId));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void UpsertExposures_And_GetCrossSection_WorksCorrectly()
    {
        if (_connection is null) return;

        var userId = Guid.NewGuid().ToString("N");
        var repo = new FactorRepository(_connection, new MemoryCache(new MemoryCacheOptions()));

        try
        {
            SeedUser(userId);

            // Define prerequisites
            repo.DefineFactor(new FactorDefinition
            {
                UserId = userId, FactorCode = "PE", FactorName = "PE", Category = FactorCategory.Value, CreatedAt = DateTimeOffset.UtcNow
            });
            repo.DefineFactor(new FactorDefinition
            {
                UserId = userId, FactorCode = "ROE", FactorName = "ROE", Category = FactorCategory.Quality, CreatedAt = DateTimeOffset.UtcNow
            });

            var date = new DateOnly(2026, 10, 5);

            // 1. Batch Upsert
            var exposures = new List<FactorExposure>
            {
                new() { UserId = userId, Symbol = "000001", FactorCode = "PE", DataDate = date, RawValue = 15.2m, ZScore = 0.5m, UpdatedAt = DateTimeOffset.UtcNow },
                new() { UserId = userId, Symbol = "000002", FactorCode = "PE", DataDate = date, RawValue = 20.1m, ZScore = -0.1m, UpdatedAt = DateTimeOffset.UtcNow },
                new() { UserId = userId, Symbol = "000001", FactorCode = "ROE", DataDate = date, RawValue = 0.15m, ZScore = 1.2m, UpdatedAt = DateTimeOffset.UtcNow }
            };

            repo.UpsertExposures(userId, exposures);

            // 2. Query Full Cross Section
            var all = repo.GetCrossSection(userId, date);
            Assert.Equal(3, all.Count);
            
            // 3. Query with category filter
            var valueFactors = repo.GetCrossSection(userId, date, FactorCategory.Value);
            Assert.Equal(2, valueFactors.Count);
            Assert.All(valueFactors, f => Assert.Equal("PE", f.FactorCode));

            var qualityFactors = repo.GetCrossSection(userId, date, FactorCategory.Quality);
            Assert.Single(qualityFactors);
            Assert.Equal("ROE", qualityFactors[0].FactorCode);

            // 4. Upsert (update) existing exposure
            repo.UpsertExposures(userId, new[]
            {
                new FactorExposure { UserId = userId, Symbol = "000001", FactorCode = "PE", DataDate = date, RawValue = 14.8m, ZScore = 0.6m, UpdatedAt = DateTimeOffset.UtcNow }
            });

            var updated = repo.GetCrossSection(userId, date).Single(f => f.Symbol == "000001" && f.FactorCode == "PE");
            Assert.Equal(14.8m, updated.RawValue);
            Assert.Equal(0.6m, updated.ZScore);
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

        using var cmd1 = conn.CreateCommand();
        cmd1.CommandText = "DELETE FROM factor_exposures WHERE user_id = $1";
        cmd1.Parameters.AddWithValue(userId);
        cmd1.ExecuteNonQuery();

        using var cmd2 = conn.CreateCommand();
        cmd2.CommandText = "DELETE FROM factor_definitions WHERE user_id = $1";
        cmd2.Parameters.AddWithValue(userId);
        cmd2.ExecuteNonQuery();

        using var cmd3 = conn.CreateCommand();
        cmd3.CommandText = "DELETE FROM users WHERE id = $1";
        cmd3.Parameters.AddWithValue(userId);
        cmd3.ExecuteNonQuery();
    }
}
