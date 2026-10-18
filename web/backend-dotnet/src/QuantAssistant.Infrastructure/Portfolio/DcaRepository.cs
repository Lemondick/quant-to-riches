using Npgsql;
using QuantAssistant.Core.Portfolio;

namespace QuantAssistant.Infrastructure.Portfolio;

public class DcaRepository(string connectionString) : IDcaRepository
{
    public void CreatePlan(DcaPlan plan)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO dca_plans (id, user_id, symbol, benchmark_symbol, base_amount, ma_days, min_multiplier, max_multiplier, status, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
            """;
        cmd.Parameters.AddWithValue(plan.Id);
        cmd.Parameters.AddWithValue(plan.UserId);
        cmd.Parameters.AddWithValue(plan.Symbol);
        cmd.Parameters.AddWithValue(plan.BenchmarkSymbol ?? (object)DBNull.Value);
        cmd.Parameters.AddWithValue(plan.BaseAmount);
        cmd.Parameters.AddWithValue(plan.MaDays);
        cmd.Parameters.AddWithValue(plan.MinMultiplier);
        cmd.Parameters.AddWithValue(plan.MaxMultiplier);
        cmd.Parameters.AddWithValue(plan.Status);
        
        cmd.ExecuteNonQuery();
    }

    public List<DcaPlan> ListActivePlans(string userId)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT id, user_id, symbol, benchmark_symbol, base_amount, ma_days, min_multiplier, max_multiplier, status FROM dca_plans WHERE user_id = $1 AND status = 'active'";
        cmd.Parameters.AddWithValue(userId);
        
        var list = new List<DcaPlan>();
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            list.Add(new DcaPlan
            {
                Id = reader.GetString(0),
                UserId = reader.GetString(1),
                Symbol = reader.GetString(2),
                BenchmarkSymbol = reader.IsDBNull(3) ? "" : reader.GetString(3),
                BaseAmount = reader.GetDecimal(4),
                MaDays = reader.GetInt32(5),
                MinMultiplier = reader.GetDecimal(6),
                MaxMultiplier = reader.GetDecimal(7),
                Status = reader.GetString(8)
            });
        }
        return list;
    }

    public void UpdatePlanStatus(string userId, string planId, string status)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "UPDATE dca_plans SET status = $1, updated_at = NOW() WHERE user_id = $2 AND id = $3";
        cmd.Parameters.AddWithValue(status);
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(planId);
        cmd.ExecuteNonQuery();
    }
}
