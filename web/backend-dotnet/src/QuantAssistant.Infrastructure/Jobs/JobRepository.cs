using Npgsql;
using QuantAssistant.Core.Jobs;

namespace QuantAssistant.Infrastructure.Jobs;

/// <summary>PostgreSQL 任务仓储：入队/领取/结果提交/取消/重启恢复。SQL 参数化，按 user_id 隔离。</summary>
public sealed class JobRepository(string connectionString) : IJobRepository
{
    public (string JobId, bool Existing) Enqueue(string userId, string cacheKey, string symbol, string snapshotId, string provider, string model, DateOnly quotaDate, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using (var gate = conn.CreateCommand())
        {
            gate.Transaction = tx;
            gate.CommandText = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))";
            gate.Parameters.AddWithValue("research:" + userId);
            gate.ExecuteNonQuery();
        }
        string? existingJobId = null;
        using (var q = conn.CreateCommand())
        {
            q.Transaction = tx;
            q.CommandText = "SELECT job_id FROM agent_research_jobs WHERE user_id = $1 AND cache_key = $2";
            q.Parameters.AddWithValue(userId);
            q.Parameters.AddWithValue(cacheKey);
            var r = q.ExecuteScalar();
            if (r is not null && r is not DBNull)
            {
                existingJobId = (string)r;
            }
        }

        if (existingJobId is not null)
        {
            tx.Commit();
            return (existingJobId, true);
        }

        using (var c = conn.CreateCommand())
        {
            c.Transaction = tx;
            c.CommandText = "SELECT count(*) FROM agent_research_attempts WHERE user_id = $1 AND quota_date = $2";
            c.Parameters.AddWithValue(userId);
            c.Parameters.AddWithValue(quotaDate);
            if (Convert.ToInt32(c.ExecuteScalar()) >= 10)
            {
                tx.Rollback();
                throw new JobQuotaExceeded();
            }
        }

        var jobId = Guid.NewGuid().ToString("N");
        using (var insJob = conn.CreateCommand())
        {
            insJob.Transaction = tx;
            insJob.CommandText = "INSERT INTO agent_research_jobs (user_id, job_id, cache_key, symbol, status, attempt_no, phase, created_at) VALUES ($1,$2,$3,$4,'queued',1,'queued',$5)";
            insJob.Parameters.AddWithValue(userId);
            insJob.Parameters.AddWithValue(jobId);
            insJob.Parameters.AddWithValue(cacheKey);
            insJob.Parameters.AddWithValue(symbol);
            insJob.Parameters.AddWithValue(now);
            insJob.ExecuteNonQuery();
        }

        using (var insAttempt = conn.CreateCommand())
        {
            insAttempt.Transaction = tx;
            insAttempt.CommandText = "INSERT INTO agent_research_attempts (user_id, job_id, attempt_no, snapshot_id, quota_date, started_at, execution_status, provider, model, prompt_version, upstream_commit) VALUES ($1,$2,1,$3,$4,$5,'queued',$6,$7,'p1','commit')";
            insAttempt.Parameters.AddWithValue(userId);
            insAttempt.Parameters.AddWithValue(jobId);
            insAttempt.Parameters.AddWithValue(snapshotId);
            insAttempt.Parameters.AddWithValue(quotaDate);
            insAttempt.Parameters.AddWithValue(now);
            insAttempt.Parameters.AddWithValue(provider);
            insAttempt.Parameters.AddWithValue(model);
            insAttempt.ExecuteNonQuery();
        }

        tx.Commit();
        return (jobId, false);
    }

    public ResearchJob? NextJob(string userId, DateTime deadline)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using (var gate = conn.CreateCommand())
        {
            gate.Transaction = tx;
            gate.CommandText = "SELECT pg_advisory_xact_lock(hashtextextended('research:global-model-limit', 0))";
            gate.ExecuteNonQuery();
            gate.CommandText = "SELECT count(*) FROM agent_research_jobs WHERE status IN ('running','cancel_requested')";
            if (Convert.ToInt32(gate.ExecuteScalar()) >= 3) return null;
        }
        string? jobId = null;
        int attempt = 0;
        using (var q = conn.CreateCommand())
        {
            q.Transaction = tx;
            q.CommandText = "SELECT job_id, attempt_no FROM agent_research_jobs WHERE user_id = $1 AND status = 'queued' ORDER BY created_at, job_id LIMIT 1 FOR UPDATE SKIP LOCKED";
            q.Parameters.AddWithValue(userId);
            using (var r = q.ExecuteReader())
            {
                if (r.Read())
                {
                    jobId = r.GetString(0);
                    attempt = r.GetInt32(1);
                }
            }
        }

        if (jobId is null)
        {
            tx.Rollback();
            return null;
        }

        using (var upd = conn.CreateCommand())
        {
            upd.Transaction = tx;
            upd.CommandText = "UPDATE agent_research_jobs SET status='running', phase='research', deadline=$1 WHERE user_id=$2 AND job_id=$3";
            upd.Parameters.AddWithValue(deadline);
            upd.Parameters.AddWithValue(userId);
            upd.Parameters.AddWithValue(jobId);
            upd.ExecuteNonQuery();
        }

        using (var updA = conn.CreateCommand())
        {
            updA.Transaction = tx;
            updA.CommandText = "UPDATE agent_research_attempts SET execution_status='running' WHERE user_id=$1 AND job_id=$2 AND attempt_no=$3";
            updA.Parameters.AddWithValue(userId);
            updA.Parameters.AddWithValue(jobId);
            updA.Parameters.AddWithValue(attempt);
            updA.ExecuteNonQuery();
        }

        tx.Commit();
        using var get = Open();
        using var g = get.CreateCommand();
        g.CommandText = "SELECT job_id, cache_key, symbol, status, attempt_no, phase FROM agent_research_jobs WHERE user_id=$1 AND job_id=$2";
        g.Parameters.AddWithValue(userId);
        g.Parameters.AddWithValue(jobId);
        using var reader = g.ExecuteReader();
        reader.Read();
        return new ResearchJob(reader.GetString(0), reader.GetString(1), reader.GetString(2), reader.GetString(3), reader.GetInt32(4), reader.GetString(5));
    }

    public bool Finish(string userId, string jobId, int attempt, string status, string? error, string? reportJson, string? usageJson, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        string? currentStatus = null;
        int currentAttempt = 0;
        DateTime? deadline = null;
        using (var q = conn.CreateCommand())
        {
            q.Transaction = tx;
            q.CommandText = "SELECT status, attempt_no, deadline FROM agent_research_jobs WHERE user_id=$1 AND job_id=$2 FOR UPDATE";
            q.Parameters.AddWithValue(userId);
            q.Parameters.AddWithValue(jobId);
            using (var r = q.ExecuteReader())
            {
                if (r.Read())
                {
                    currentStatus = r.GetString(0);
                    currentAttempt = r.GetInt32(1);
                    deadline = r.IsDBNull(2) ? null : r.GetDateTime(2);
                }
            }
        }

        if (currentStatus is null)
        {
            tx.Rollback();
            return false;
        }

        if (currentAttempt != attempt || currentStatus is not (JobStatus.Running or JobStatus.CancelRequested)
            || deadline is null || deadline <= now
            || status is not (JobStatus.Completed or JobStatus.Failed or JobStatus.Cancelled or JobStatus.Interrupted))
        {
            tx.Rollback();
            return false;
        }

        if (currentStatus == JobStatus.CancelRequested)
        {
            status = JobStatus.Cancelled;
            error = "cancelled";
            reportJson = null;
            usageJson = null;
        }

        using (var upd = conn.CreateCommand())
        {
            upd.Transaction = tx;
            upd.CommandText = "UPDATE agent_research_jobs SET status=$1, phase=$1, error_code=$2 WHERE user_id=$3 AND job_id=$4";
            upd.Parameters.AddWithValue(status);
            upd.Parameters.AddWithValue((object?)error ?? DBNull.Value);
            upd.Parameters.AddWithValue(userId);
            upd.Parameters.AddWithValue(jobId);
            upd.ExecuteNonQuery();
        }

        using (var updA = conn.CreateCommand())
        {
            updA.Transaction = tx;
            updA.CommandText = "UPDATE agent_research_attempts SET execution_status=$1, report_json=$2::jsonb, usage_json=$3::jsonb, completed_at=$4 WHERE user_id=$5 AND job_id=$6 AND attempt_no=$7";
            updA.Parameters.AddWithValue(status);
            updA.Parameters.AddWithValue((object?)reportJson ?? DBNull.Value);
            updA.Parameters.AddWithValue((object?)usageJson ?? DBNull.Value);
            updA.Parameters.AddWithValue(now);
            updA.Parameters.AddWithValue(userId);
            updA.Parameters.AddWithValue(jobId);
            updA.Parameters.AddWithValue(attempt);
            updA.ExecuteNonQuery();
        }

        tx.Commit();
        return true;
    }

    public void Cancel(string userId, string jobId, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        string? status = null;
        int attempt = 0;
        using (var q = conn.CreateCommand())
        {
            q.Transaction = tx;
            q.CommandText = "SELECT status, attempt_no FROM agent_research_jobs WHERE user_id=$1 AND job_id=$2 FOR UPDATE";
            q.Parameters.AddWithValue(userId);
            q.Parameters.AddWithValue(jobId);
            using (var r = q.ExecuteReader())
            {
                if (r.Read())
                {
                    status = r.GetString(0);
                    attempt = r.GetInt32(1);
                }
            }
        }

        if (status is null)
        {
            tx.Rollback();
            throw new JobNotFoundError(jobId);
        }

        if (status == JobStatus.Queued)
        {
            using var upd = conn.CreateCommand();
            upd.Transaction = tx;
            upd.CommandText = "UPDATE agent_research_jobs SET status='cancelled', phase='cancelled', error_code='cancelled' WHERE user_id=$1 AND job_id=$2";
            upd.Parameters.AddWithValue(userId);
            upd.Parameters.AddWithValue(jobId);
            upd.ExecuteNonQuery();
        }
        else if (status == JobStatus.Running)
        {
            using var upd = conn.CreateCommand();
            upd.Transaction = tx;
            upd.CommandText = "UPDATE agent_research_jobs SET status='cancel_requested' WHERE user_id=$1 AND job_id=$2";
            upd.Parameters.AddWithValue(userId);
            upd.Parameters.AddWithValue(jobId);
            upd.ExecuteNonQuery();
        }

        tx.Commit();
    }

    public void Recover(string userId, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using (var updA = conn.CreateCommand())
        {
            updA.Transaction = tx;
            updA.CommandText = "UPDATE agent_research_attempts SET execution_status='interrupted', completed_at=$1 WHERE user_id=$2 AND execution_status IN ('queued','running')";
            updA.Parameters.AddWithValue(now);
            updA.Parameters.AddWithValue(userId);
            updA.ExecuteNonQuery();
        }

        using (var upd = conn.CreateCommand())
        {
            upd.Transaction = tx;
            upd.CommandText = "UPDATE agent_research_jobs SET status='interrupted', phase='interrupted', error_code='interrupted' WHERE user_id=$1 AND status IN ('queued','running','cancel_requested')";
            upd.Parameters.AddWithValue(userId);
            upd.ExecuteNonQuery();
        }

        tx.Commit();
    }

    public ResearchJobDetail Get(string userId, string jobId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT j.job_id, j.symbol, j.status, j.attempt_no, j.error_code, a.report_json, a.usage_json, j.created_at, a.completed_at " +
            "FROM agent_research_jobs j LEFT JOIN agent_research_attempts a ON a.user_id=j.user_id AND a.job_id=j.job_id AND a.attempt_no=j.attempt_no " +
            "WHERE j.user_id=$1 AND j.job_id=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(jobId);
        using var r = cmd.ExecuteReader();
        if (!r.Read())
        {
            throw new JobNotFoundError(jobId);
        }

        return new ResearchJobDetail(
            r.GetString(0),
            r.GetString(1),
            r.GetString(2),
            r.GetInt32(3),
            r.IsDBNull(4) ? null : r.GetString(4),
            r.IsDBNull(5) ? null : r.GetString(5),
            r.IsDBNull(6) ? null : r.GetString(6),
            r.GetFieldValue<DateTime>(7),
            r.IsDBNull(8) ? null : r.GetFieldValue<DateTime>(8));
    }

    private NpgsqlConnection Open()
    {
        var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        return conn;
    }
}
