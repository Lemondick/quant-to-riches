using Npgsql;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Operations;

/// <summary>PostgreSQL 运行协调仓储：租约锁 + 任务心跳。SQL 参数化，按 user_id 隔离。</summary>
public sealed class OperationRepository(string connectionString) : IOperationRepository
{
    public void AcquireLock(string userId, string lockName, string ownerToken, DateTime acquiredAt, DateTime expiresAt)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using (var del = conn.CreateCommand())
        {
            del.Transaction = tx;
            del.CommandText = "DELETE FROM operation_locks WHERE user_id = $1 AND lock_name = $2 AND expires_at <= $3";
            del.Parameters.AddWithValue(userId);
            del.Parameters.AddWithValue(lockName);
            del.Parameters.AddWithValue(acquiredAt);
            del.ExecuteNonQuery();
        }

        using (var ins = conn.CreateCommand())
        {
            ins.Transaction = tx;
            ins.CommandText = "INSERT INTO operation_locks (user_id, lock_name, owner_token, acquired_at, expires_at) VALUES ($1,$2,$3,$4,$5)";
            ins.Parameters.AddWithValue(userId);
            ins.Parameters.AddWithValue(lockName);
            ins.Parameters.AddWithValue(ownerToken);
            ins.Parameters.AddWithValue(acquiredAt);
            ins.Parameters.AddWithValue(expiresAt);
            try
            {
                ins.ExecuteNonQuery();
            }
            catch (PostgresException ex) when (ex.SqlState == PostgresErrorCodes.UniqueViolation)
            {
                tx.Rollback();
                throw new OperationAlreadyRunningError(lockName);
            }
        }

        tx.Commit();
    }

    public void ReleaseLock(string userId, string lockName, string ownerToken)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM operation_locks WHERE user_id = $1 AND lock_name = $2 AND owner_token = $3";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(lockName);
        cmd.Parameters.AddWithValue(ownerToken);
        cmd.ExecuteNonQuery();
    }

    public void UpsertHeartbeat(string userId, string jobName, int expectedIntervalMinutes, DateTime startedAt, DateTime completedAt, string status, string? error)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO job_heartbeats (user_id, job_name, expected_interval_minutes, last_started_at, last_completed_at, last_status, last_error, updated_at) " +
            "VALUES ($1,$2,$3,$4,$5,$6,$7,now()) " +
            "ON CONFLICT (user_id, job_name) DO UPDATE SET last_started_at = EXCLUDED.last_started_at, last_completed_at = EXCLUDED.last_completed_at, " +
            "last_status = EXCLUDED.last_status, last_error = EXCLUDED.last_error, updated_at = now()";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(jobName);
        cmd.Parameters.AddWithValue(expectedIntervalMinutes);
        cmd.Parameters.AddWithValue(startedAt);
        cmd.Parameters.AddWithValue(completedAt);
        cmd.Parameters.AddWithValue(status);
        cmd.Parameters.AddWithValue((object?)error ?? DBNull.Value);
        cmd.ExecuteNonQuery();
    }

    private NpgsqlConnection Open()
    {
        var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        return conn;
    }
}
