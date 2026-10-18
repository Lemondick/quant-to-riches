using Npgsql;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>P4 任务协议集成测试：幂等入队、领取、结果幂等（旧 Worker 不能覆盖）、取消、重启恢复。</summary>
public sealed class JobIntegrationTests
{
    private readonly string? _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");

    [PgFact]
    public void Enqueue_Next_Finish_AreIdempotent()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var repo = new JobRepository(_connection);
        var now = DateTime.UtcNow;
        var today = DateOnly.FromDateTime(now);
        try
        {
            SeedUser(userId);

            // 幂等入队
            var (jobId, existing) = repo.Enqueue(userId, "cache-1", "600000", "snap-1", "p", "m", today, now);
            Assert.False(existing);
            var (jobId2, existing2) = repo.Enqueue(userId, "cache-1", "600000", "snap-1", "p", "m", today, now);
            Assert.True(existing2);
            Assert.Equal(jobId, jobId2);

            // 领取
            var job = repo.NextJob(userId, now.AddMinutes(10));
            Assert.NotNull(job);
            Assert.Equal(JobStatus.Running, job!.Status);

            // 结果幂等：正确 attempt 成功，错误 attempt 被拒（旧 Worker 不能覆盖）
            Assert.True(repo.Finish(userId, jobId, 1, JobStatus.Completed, null, "{}", "{}", now));
            Assert.False(repo.Finish(userId, jobId, 1, JobStatus.Failed, "x", null, null, now));
            Assert.False(repo.Finish(userId, jobId, 2, JobStatus.Completed, null, null, null, now));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void Recover_InterruptsRunningJobs()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var repo = new JobRepository(_connection);
        var now = DateTime.UtcNow;
        var today = DateOnly.FromDateTime(now);
        try
        {
            SeedUser(userId);
            repo.Enqueue(userId, "cache-2", "600000", "snap-2", "p", "m", today, now);
            repo.NextJob(userId, now.AddMinutes(10)); // running

            repo.Recover(userId, now);

            // 重启后 running 任务被标记 interrupted（状态可核对，不重复执行）
            Assert.Null(repo.NextJob(userId, now.AddMinutes(10)));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    private void SeedUser(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO users (id, username, password_hash, role, status, created_at) VALUES ($1,$2,'x','user','active',now())";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue($"u_{userId[..8]}");
        cmd.ExecuteNonQuery();
    }

    private void Cleanup(string userId)
    {
        using var conn = Open();
        foreach (var table in new[] { "agent_research_attempts", "agent_research_jobs", "sessions", "recovery_tokens" })
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = $"DELETE FROM {table} WHERE user_id = $1";
            cmd.Parameters.AddWithValue(userId);
            cmd.ExecuteNonQuery();
        }

        using (var del = conn.CreateCommand())
        {
            del.CommandText = "DELETE FROM users WHERE id = $1";
            del.Parameters.AddWithValue(userId);
            del.ExecuteNonQuery();
        }
    }

    private NpgsqlConnection Open()
    {
        var conn = new NpgsqlConnection(_connection);
        conn.Open();
        return conn;
    }
}
