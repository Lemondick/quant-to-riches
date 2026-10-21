using Npgsql;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Operations;
using QuantAssistant.Infrastructure.Operations;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>P4 集成测试：租约排他、释放后可重入、心跳。真实 PG，QUANT_TEST_PG_CONNECTION 门控。</summary>
public sealed class OperationIntegrationTests
{
    private readonly string? _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");

    [PgFact]
    public void Lease_IsExclusive_AndReentrantAfterRelease()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var coordinator = new OperationCoordinator(new OperationRepository(_connection), TimeProvider.System);
        try
        {
            SeedUser(userId);
            using (var first = coordinator.Hold(userId, "job-lock", TimeSpan.FromMinutes(30)))
            {
                // 同锁名再持有 → 冲突
                Assert.Throws<OperationAlreadyRunningError>(() => coordinator.Hold(userId, "job-lock", TimeSpan.FromMinutes(30)));
                Assert.False(string.IsNullOrEmpty(first.OwnerToken));
            }

            // 释放后可重入
            using var second = coordinator.Hold(userId, "job-lock", TimeSpan.FromMinutes(30));
            Assert.NotNull(second);
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void Heartbeat_Upserts()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var repo = new OperationRepository(_connection);
        try
        {
            SeedUser(userId);
            var now = DateTime.UtcNow;
            // 状态枚举以 OperationsViewsController 为准：healthy/failed/blocked（迁移 0045 已对齐 DB 约束）。
            repo.UpsertHeartbeat(userId, "daily-report", 1440, now.AddMinutes(-1), now, "healthy", null);
            repo.UpsertHeartbeat(userId, "daily-report", 1440, now, now, "failed", "synthetic");
            // 不抛异常即 upsert 成功
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
        foreach (var table in new[] { "job_heartbeats", "operation_locks", "sessions", "recovery_tokens" })
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
