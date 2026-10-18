using Npgsql;
using QuantAssistant.Core.Runtime;

namespace QuantAssistant.Infrastructure.Runtime;

/// <summary>PostgreSQL 运行维护仓储：管理员运行指标聚合。SQL 参数化。</summary>
public sealed class RuntimeRepository(string connectionString) : IRuntimeRepository
{
    public IReadOnlyList<AccountRuntime> Accounts()
    {
        var result = new List<AccountRuntime>();
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT u.id, u.username, u.status, " +
            "COALESCE((SELECT count(*) FROM market_advisor_reports r WHERE r.user_id=u.id AND r.status='failed'),0) AS advisor_failures, " +
            "COALESCE((SELECT count(*) FROM agent_research_jobs j WHERE j.user_id=u.id AND j.status IN ('failed','timed_out','interrupted')),0) AS research_failures " +
            "FROM users u ORDER BY u.created_at";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            result.Add(new AccountRuntime(
                r.GetString(0),
                r.GetString(1),
                r.GetString(2),
                "ready",
                r.GetInt32(3),
                r.GetInt32(4)));
        }

        return result;
    }

    public bool BusinessReady()
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT count(*) FROM users WHERE status='active'";
        return Convert.ToInt32(cmd.ExecuteScalar()) >= 0;
    }

    private NpgsqlConnection Open()
    {
        var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        return conn;
    }
}
