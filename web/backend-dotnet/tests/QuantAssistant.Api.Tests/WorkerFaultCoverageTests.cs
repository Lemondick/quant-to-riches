using Npgsql;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Operations;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// P4 Mock 模型故障覆盖（计划书 §7 验收表）：模型超时、Worker 退出、DB 中断回滚、旧租约、未知计费。
/// 用真实 PG 验证任务协议在故障场景下的行为语义，不调用真实模型。
/// </summary>
public sealed class WorkerFaultCoverageTests
{
    private readonly string? _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");

    [PgFact]
    public void StaleLease_DoesNotBlockNewWorker()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var ops = new OperationRepository(_connection);
        var now = DateTime.UtcNow;
        try
        {
            SeedUser(userId);

            // 旧 Worker 获取租约后异常退出（未释放），租约已过期
            ops.AcquireLock(userId, "research", "old-worker", now.AddMinutes(-30), now.AddMinutes(-20));

            // 新 Worker 应能绕过过期租约重新获取（删除过期锁 + 插入）
            ops.AcquireLock(userId, "research", "new-worker", now, now.AddMinutes(10));

            // 释放后旧租约已被覆盖，只有新 Worker 的锁存在
            ops.ReleaseLock(userId, "research", "new-worker");
            Assert.False(LockExists(userId, "research"));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void ModelTimeout_RecoversToInterrupted_AndDoesNotRerun()
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
            repo.Enqueue(userId, "cache-timeout", "600000", "snap", "p", "m", today, now);

            // Worker 领取后模型超时（deadline 已过，Worker 崩溃未提交结果）
            repo.NextJob(userId, now.AddSeconds(1));

            // 重启恢复：running 任务标记 interrupted，且不重复执行
            repo.Recover(userId, now.AddMinutes(5));
            Assert.Null(repo.NextJob(userId, now.AddMinutes(6)));
            Assert.Equal("interrupted", JobStatusOf(userId, "cache-timeout"));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void UnknownBilling_FinishesWithoutUsage()
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
            var (jobId, _) = repo.Enqueue(userId, "cache-billing", "600000", "snap", "p", "m", today, now);
            repo.NextJob(userId, now.AddMinutes(10));

            // 未知计费：usage=null（上游未回传计费），结果仍应正常提交不崩溃
            Assert.True(repo.Finish(userId, jobId, 1, JobStatus.Completed, null, "{}", null, now));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void QuotaExceeded_RollsBackAtomically_NoPartialRows()
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

            // 塞满当日配额（10）
            for (var i = 0; i < 10; i++)
            {
                repo.Enqueue(userId, $"cache-{i}", "600000", "snap", "p", "m", today, now);
            }

            // 第 11 次应抛配额超限，且事务回滚不产生半成品 job/attempt
            Assert.Throws<JobQuotaExceeded>(() =>
                repo.Enqueue(userId, "cache-overflow", "600000", "snap", "p", "m", today, now));

            Assert.Equal(10, CountRows(userId, "agent_research_jobs"));
            Assert.Equal(10, CountRows(userId, "agent_research_attempts"));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void WorkerExit_CancelRequested_FinishBecomesCancelled()
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
            var (jobId, _) = repo.Enqueue(userId, "cache-cancel", "600000", "snap", "p", "m", today, now);
            repo.NextJob(userId, now.AddMinutes(10));

            // 用户在 running 期间取消 → cancel_requested；Worker 提交结果时被强制转 cancelled
            repo.Cancel(userId, jobId, now);
            Assert.True(repo.Finish(userId, jobId, 1, JobStatus.Completed, null, "{}", "{}", now));
            Assert.Equal("cancelled", JobStatusOf(userId, "cache-cancel"));
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

    private bool LockExists(string userId, string lockName)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT count(*) FROM operation_locks WHERE user_id=$1 AND lock_name=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(lockName);
        return Convert.ToInt32(cmd.ExecuteScalar()) > 0;
    }

    private string? JobStatusOf(string userId, string cacheKey)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT status FROM agent_research_jobs WHERE user_id=$1 AND cache_key=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(cacheKey);
        return cmd.ExecuteScalar() as string;
    }

    private int CountRows(string userId, string table)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = $"SELECT count(*) FROM {table} WHERE user_id=$1";
        cmd.Parameters.AddWithValue(userId);
        return Convert.ToInt32(cmd.ExecuteScalar());
    }

    private void Cleanup(string userId)
    {
        using var conn = Open();
        foreach (var table in new[] { "operation_locks", "job_heartbeats", "agent_research_attempts", "agent_research_jobs", "sessions", "recovery_tokens" })
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
