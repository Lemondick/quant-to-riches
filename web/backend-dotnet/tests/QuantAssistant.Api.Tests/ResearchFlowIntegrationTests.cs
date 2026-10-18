using Npgsql;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>P5 研究任务 HTTP 门面的底层链路：enqueue→get→finish→cancel→retry。</summary>
public sealed class ResearchFlowIntegrationTests
{
    private readonly string? _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");

    [PgFact]
    public void Create_Get_Finish_Lifecycle()
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

            var (jobId, existing) = repo.Enqueue(userId, "cache-research", "600000.SH", "snap-1", "mock", "default", today, now);
            Assert.False(existing);

            // get：queued 详情
            var detail = repo.Get(userId, jobId);
            Assert.Equal("queued", detail.Status);
            Assert.Equal("600000.SH", detail.Symbol);
            Assert.Null(detail.ReportJson);

            // 领取 → running
            repo.NextJob(userId, now.AddMinutes(10));
            Assert.Equal("running", repo.Get(userId, jobId).Status);

            // 提交结果 → completed + report
            Assert.True(repo.Finish(userId, jobId, 1, JobStatus.Completed, null, "{\"ok\":true}", "{\"tokens\":1}", now));
            var done = repo.Get(userId, jobId);
            Assert.Equal("completed", done.Status);
            Assert.Equal("{\"ok\": true}", done.ReportJson); // PG jsonb 规范化输出
            Assert.NotNull(done.CompletedAt);
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void Retry_ProducesNewJobId()
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
            var (jobId, _) = repo.Enqueue(userId, "cache-retry", "600000.SH", "snap-1", "mock", "default", today, now);

            // 用新 snapshot 重试 → 新 cache_key → 新 job
            var (retryId, retryExisting) = repo.Enqueue(userId, "cache-retry-2", "600000.SH", "snap-2", "mock", "default", today, now);
            Assert.False(retryExisting);
            Assert.NotEqual(jobId, retryId);
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
