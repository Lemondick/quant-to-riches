using Microsoft.Extensions.Caching.Memory;
using Npgsql;
using Polly;
using Polly.Retry;
using QuantAssistant.Core.Portfolio;

namespace QuantAssistant.Infrastructure.Portfolio;

public class PeerRepository : IPeerRepository
{
    private readonly string _connectionString;
    private readonly IMemoryCache _cache;
    private readonly ResiliencePipeline _retryPipeline;

    public PeerRepository(string connectionString, IMemoryCache cache)
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

    public void CreateGroup(PeerGroup group)
    {
        _retryPipeline.Execute(() =>
        {
            using var conn = new NpgsqlConnection(_connectionString);
            conn.Open();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "INSERT INTO peer_groups (id, group_name, benchmark_symbol) VALUES ($1, $2, $3)";
            cmd.Parameters.AddWithValue(group.Id);
            cmd.Parameters.AddWithValue(group.GroupName);
            cmd.Parameters.AddWithValue(group.BenchmarkSymbol ?? (object)DBNull.Value);
            cmd.ExecuteNonQuery();
        });
    }

    public void AssignFundToGroup(string symbol, string groupId)
    {
        _retryPipeline.Execute(() =>
        {
            using var conn = new NpgsqlConnection(_connectionString);
            conn.Open();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "INSERT INTO fund_peer_mapping (symbol, group_id) VALUES ($1, $2) ON CONFLICT DO NOTHING";
            cmd.Parameters.AddWithValue(symbol);
            cmd.Parameters.AddWithValue(groupId);
            cmd.ExecuteNonQuery();
        });
    }

    public void UpsertRankings(IEnumerable<PeerRanking> rankings)
    {
        var rows = rankings.ToArray();
        _retryPipeline.Execute(() =>
        {
            using var conn = new NpgsqlConnection(_connectionString);
            conn.Open();
            using var batch = conn.CreateBatch();
            foreach (var r in rows)
            {
                var cmd = batch.CreateBatchCommand();
                cmd.CommandText = """
                    INSERT INTO peer_rankings (symbol, group_id, data_date, return_1m, rank_1m, percentile_1m, return_1y, rank_1y, percentile_1y, excess_return_1y, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                    ON CONFLICT (symbol, group_id, data_date) DO UPDATE SET 
                        return_1m = EXCLUDED.return_1m,
                        rank_1m = EXCLUDED.rank_1m,
                        percentile_1m = EXCLUDED.percentile_1m,
                        return_1y = EXCLUDED.return_1y,
                        rank_1y = EXCLUDED.rank_1y,
                        percentile_1y = EXCLUDED.percentile_1y,
                        excess_return_1y = EXCLUDED.excess_return_1y,
                        updated_at = EXCLUDED.updated_at
                    """;
                cmd.Parameters.AddWithValue(r.Symbol);
                cmd.Parameters.AddWithValue(r.GroupId);
                cmd.Parameters.AddWithValue(r.DataDate);
                cmd.Parameters.AddWithValue(r.Return1M ?? (object)DBNull.Value);
                cmd.Parameters.AddWithValue(r.Rank1M ?? (object)DBNull.Value);
                cmd.Parameters.AddWithValue(r.Percentile1M ?? (object)DBNull.Value);
                cmd.Parameters.AddWithValue(r.Return1Y ?? (object)DBNull.Value);
                cmd.Parameters.AddWithValue(r.Rank1Y ?? (object)DBNull.Value);
                cmd.Parameters.AddWithValue(r.Percentile1Y ?? (object)DBNull.Value);
                cmd.Parameters.AddWithValue(r.ExcessReturn1Y ?? (object)DBNull.Value);
                batch.BatchCommands.Add(cmd);
            }
            batch.ExecuteNonQuery();
        });
        foreach (var row in rows)
        {
            _cache.Remove($"PeerRankings_{row.GroupId}_{row.DataDate:yyyyMMdd}");
            _cache.Remove($"PeerRanking_{row.Symbol}_{row.GroupId}_{row.DataDate:yyyyMMdd}");
        }
    }

    public List<PeerRanking> GetRankingsByGroup(string groupId, DateOnly date)
    {
        var cacheKey = $"PeerRankings_{groupId}_{date:yyyyMMdd}";
        
        return _cache.GetOrCreate(cacheKey, entry =>
        {
            entry.AbsoluteExpirationRelativeToNow = TimeSpan.FromMinutes(15);
            entry.SlidingExpiration = TimeSpan.FromMinutes(5);

            return _retryPipeline.Execute(() =>
            {
                using var conn = new NpgsqlConnection(_connectionString);
                conn.Open();
                using var cmd = conn.CreateCommand();
                cmd.CommandText = "SELECT symbol, group_id, data_date, return_1m, rank_1m, percentile_1m, return_1y, rank_1y, percentile_1y, excess_return_1y FROM peer_rankings WHERE group_id = $1 AND data_date = $2";
                cmd.Parameters.AddWithValue(groupId);
                cmd.Parameters.AddWithValue(date);

                var list = new List<PeerRanking>();
                using var reader = cmd.ExecuteReader();
                while (reader.Read())
                {
                    list.Add(new PeerRanking
                    {
                        Symbol = reader.GetString(0),
                        GroupId = reader.GetString(1),
                        DataDate = reader.GetFieldValue<DateOnly>(2),
                        Return1M = reader.IsDBNull(3) ? null : reader.GetDecimal(3),
                        Rank1M = reader.IsDBNull(4) ? null : reader.GetInt32(4),
                        Percentile1M = reader.IsDBNull(5) ? null : reader.GetDecimal(5),
                        Return1Y = reader.IsDBNull(6) ? null : reader.GetDecimal(6),
                        Rank1Y = reader.IsDBNull(7) ? null : reader.GetInt32(7),
                        Percentile1Y = reader.IsDBNull(8) ? null : reader.GetDecimal(8),
                        ExcessReturn1Y = reader.IsDBNull(9) ? null : reader.GetDecimal(9)
                    });
                }
                return list;
            });
        }) ?? new List<PeerRanking>();
    }

    public PeerRanking? GetFundRanking(string symbol, string groupId, DateOnly date)
    {
        var cacheKey = $"PeerRanking_{symbol}_{groupId}_{date:yyyyMMdd}";
        
        return _cache.GetOrCreate(cacheKey, entry =>
        {
            entry.AbsoluteExpirationRelativeToNow = TimeSpan.FromMinutes(15);
            entry.SlidingExpiration = TimeSpan.FromMinutes(5);

            return _retryPipeline.Execute(() =>
            {
                using var conn = new NpgsqlConnection(_connectionString);
                conn.Open();
                using var cmd = conn.CreateCommand();
                cmd.CommandText = "SELECT symbol, group_id, data_date, return_1m, rank_1m, percentile_1m, return_1y, rank_1y, percentile_1y, excess_return_1y FROM peer_rankings WHERE symbol = $1 AND group_id = $2 AND data_date = $3";
                cmd.Parameters.AddWithValue(symbol);
                cmd.Parameters.AddWithValue(groupId);
                cmd.Parameters.AddWithValue(date);

                using var reader = cmd.ExecuteReader();
                if (reader.Read())
                {
                    return new PeerRanking
                    {
                        Symbol = reader.GetString(0),
                        GroupId = reader.GetString(1),
                        DataDate = reader.GetFieldValue<DateOnly>(2),
                        Return1M = reader.IsDBNull(3) ? null : reader.GetDecimal(3),
                        Rank1M = reader.IsDBNull(4) ? null : reader.GetInt32(4),
                        Percentile1M = reader.IsDBNull(5) ? null : reader.GetDecimal(5),
                        Return1Y = reader.IsDBNull(6) ? null : reader.GetDecimal(6),
                        Rank1Y = reader.IsDBNull(7) ? null : reader.GetInt32(7),
                        Percentile1Y = reader.IsDBNull(8) ? null : reader.GetDecimal(8),
                        ExcessReturn1Y = reader.IsDBNull(9) ? null : reader.GetDecimal(9)
                    };
                }
                return null;
            });
        });
    }
}
