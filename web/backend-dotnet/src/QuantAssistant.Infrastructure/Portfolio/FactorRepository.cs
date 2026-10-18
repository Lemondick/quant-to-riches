using System.Data.Common;
using Microsoft.Extensions.Caching.Memory;
using Npgsql;
using Polly;
using Polly.Retry;
using QuantAssistant.Core.Portfolio;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>
/// 多因子时序数据库仓储实现。
/// 引入内存缓存 (MemoryCache) 和 Polly 瞬态故障重试。
/// </summary>
public sealed class FactorRepository : IFactorRepository
{
    private readonly string _connectionString;
    private readonly IMemoryCache _cache;
    private readonly ResiliencePipeline _retryPipeline;

    public FactorRepository(string connectionString, IMemoryCache cache)
    {
        _connectionString = connectionString;
        _cache = cache;
        _retryPipeline = new ResiliencePipelineBuilder()
            .AddRetry(new RetryStrategyOptions
            {
                ShouldHandle = new PredicateBuilder().Handle<NpgsqlException>(ex => ex.IsTransient),
                MaxRetryAttempts = 3,
                BackoffType = DelayBackoffType.Exponential,
                Delay = TimeSpan.FromMilliseconds(200)
            })
            .Build();
    }

    public void DefineFactor(FactorDefinition definition)
    {
        _retryPipeline.Execute(() =>
        {
            using var conn = new NpgsqlConnection(_connectionString);
            conn.Open();

            using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                INSERT INTO factor_definitions (user_id, factor_code, factor_name, category, direction, description, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (user_id, factor_code) DO UPDATE SET
                    factor_name = EXCLUDED.factor_name,
                    category = EXCLUDED.category,
                    direction = EXCLUDED.direction,
                    description = EXCLUDED.description
                """;
            
            cmd.Parameters.AddWithValue(definition.UserId);
            cmd.Parameters.AddWithValue(definition.FactorCode);
            cmd.Parameters.AddWithValue(definition.FactorName);
            cmd.Parameters.AddWithValue(definition.Category);
            cmd.Parameters.AddWithValue(definition.Direction);
            cmd.Parameters.AddWithValue(definition.Description ?? (object)DBNull.Value);
            cmd.Parameters.AddWithValue(definition.CreatedAt.UtcDateTime); 
            
            cmd.ExecuteNonQuery();
        });

        // 使字典缓存失效
        _cache.Remove($"FactorDefs_{definition.UserId}");
    }

    public void DeleteFactor(string userId, string factorCode)
    {
        _retryPipeline.Execute(() =>
        {
            using var conn = new NpgsqlConnection(_connectionString);
            conn.Open();

            using var cmd = conn.CreateCommand();
            cmd.CommandText = "DELETE FROM factor_definitions WHERE user_id = $1 AND factor_code = $2";
            cmd.Parameters.AddWithValue(userId);
            cmd.Parameters.AddWithValue(factorCode);
            cmd.ExecuteNonQuery();
        });

        _cache.Remove($"FactorDefs_{userId}");
    }

    public IReadOnlyList<FactorDefinition> ListDefinitions(string userId)
    {
        return _cache.GetOrCreate($"FactorDefs_{userId}", entry =>
        {
            entry.AbsoluteExpirationRelativeToNow = TimeSpan.FromMinutes(10);
            
            return _retryPipeline.Execute(() =>
            {
                using var conn = new NpgsqlConnection(_connectionString);
                conn.Open();

                using var cmd = conn.CreateCommand();
                cmd.CommandText = """
                    SELECT factor_code, factor_name, category, direction, description, created_at 
                    FROM factor_definitions 
                    WHERE user_id = $1 
                    ORDER BY factor_code
                    """;
                cmd.Parameters.AddWithValue(userId);

                var results = new List<FactorDefinition>();
                using var reader = cmd.ExecuteReader();
                while (reader.Read())
                {
                    results.Add(new FactorDefinition
                    {
                        UserId = userId,
                        FactorCode = reader.GetString(0),
                        FactorName = reader.GetString(1),
                        Category = reader.GetString(2),
                        Direction = reader.GetInt32(3),
                        Description = reader.IsDBNull(4) ? null : reader.GetString(4),
                        CreatedAt = new DateTimeOffset(reader.GetFieldValue<DateTime>(5), TimeSpan.Zero)
                    });
                }
                return results;
            });
        }) ?? new List<FactorDefinition>();
    }

    public void UpsertExposures(string userId, IReadOnlyList<FactorExposure> exposures)
    {
        if (exposures.Count == 0) return;

        _retryPipeline.Execute(() =>
        {
            using var conn = new NpgsqlConnection(_connectionString);
            conn.Open();

            using var batch = conn.CreateBatch();

            foreach (var exp in exposures)
            {
                if (exp.UserId != userId)
                    throw new ArgumentException($"Exposure UserId {exp.UserId} mismatches context {userId}.");

                var cmd = batch.CreateBatchCommand();
                cmd.CommandText = """
                    INSERT INTO factor_exposures (user_id, symbol, factor_code, data_date, raw_value, z_score, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (user_id, symbol, factor_code, data_date) DO UPDATE SET
                        raw_value = EXCLUDED.raw_value,
                        z_score = EXCLUDED.z_score,
                        updated_at = EXCLUDED.updated_at
                    """;
                cmd.Parameters.AddWithValue(userId);
                cmd.Parameters.AddWithValue(exp.Symbol);
                cmd.Parameters.AddWithValue(exp.FactorCode);
                cmd.Parameters.AddWithValue(exp.DataDate);
                cmd.Parameters.AddWithValue(exp.RawValue.HasValue ? exp.RawValue.Value : DBNull.Value);
                cmd.Parameters.AddWithValue(exp.ZScore.HasValue ? exp.ZScore.Value : DBNull.Value);
                cmd.Parameters.AddWithValue(exp.UpdatedAt.UtcDateTime);
                
                batch.BatchCommands.Add(cmd);
            }

            batch.ExecuteNonQuery();
        });

    }

    public IReadOnlyList<FactorExposure> GetCrossSection(string userId, DateOnly date, string? category = null)
    {
        return _retryPipeline.Execute(() =>
            {
                using var conn = new NpgsqlConnection(_connectionString);
                conn.Open();

                using var cmd = conn.CreateCommand();
                if (string.IsNullOrWhiteSpace(category))
                {
                    cmd.CommandText = """
                        SELECT symbol, factor_code, raw_value, z_score, updated_at 
                        FROM factor_exposures 
                        WHERE user_id = $1 AND data_date = $2
                        """;
                    cmd.Parameters.AddWithValue(userId);
                    cmd.Parameters.AddWithValue(date);
                }
                else
                {
                    cmd.CommandText = """
                        SELECT e.symbol, e.factor_code, e.raw_value, e.z_score, e.updated_at 
                        FROM factor_exposures e
                        JOIN factor_definitions d ON e.user_id = d.user_id AND e.factor_code = d.factor_code
                        WHERE e.user_id = $1 AND e.data_date = $2 AND d.category = $3
                        """;
                    cmd.Parameters.AddWithValue(userId);
                    cmd.Parameters.AddWithValue(date);
                    cmd.Parameters.AddWithValue(category);
                }

                var results = new List<FactorExposure>();
                using var reader = cmd.ExecuteReader();
                while (reader.Read())
                {
                    results.Add(new FactorExposure
                    {
                        UserId = userId,
                        DataDate = date,
                        Symbol = reader.GetString(0),
                        FactorCode = reader.GetString(1),
                        RawValue = reader.IsDBNull(2) ? null : reader.GetDecimal(2),
                        ZScore = reader.IsDBNull(3) ? null : reader.GetDecimal(3),
                        UpdatedAt = new DateTimeOffset(reader.GetFieldValue<DateTime>(4), TimeSpan.Zero)
                    });
                }
                return results;
            });

    }
}
