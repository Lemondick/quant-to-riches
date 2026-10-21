using System.Text.Json.Nodes;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Services;

public sealed class NativeRuntimeMaintenance(AccountDatabase database, NativeMetrics metrics,
    IConfiguration configuration, ILogger<NativeRuntimeMaintenance> logger) : BackgroundService
{
    public static JsonObject Queue(AccountSql sql) => sql.Query("""
        SELECT count(*) FILTER(WHERE status='queued') AS queued,
          count(*) FILTER(WHERE status IN('running','cancel_requested')) AS running,
          count(*) FILTER(WHERE deadline<now()) AS overdue,
          greatest(0,COALESCE(EXTRACT(EPOCH FROM now()-min(created_at) FILTER(WHERE status='queued')),0))::bigint AS oldest_queued_seconds
        FROM compute_jobs WHERE status IN('queued','running','cancel_requested')
        """)[0]!.DeepClone().AsObject();

    public static JsonObject Database(AccountSql sql) => sql.Query("""
        SELECT numbackends AS connections, deadlocks, temp_bytes,
          (pg_has_role(current_user,'pg_read_all_stats','MEMBER') OR (SELECT usesuper FROM pg_user WHERE usename=current_user)) AS all_session_statistics,
          (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND cardinality(pg_blocking_pids(pid))>0) AS blocked_connections,
          CASE WHEN pg_has_role(current_user,'pg_read_all_stats','MEMBER') OR (SELECT usesuper FROM pg_user WHERE usename=current_user)
            THEN (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction') END AS idle_transactions,
          CASE WHEN pg_has_role(current_user,'pg_read_all_stats','MEMBER') OR (SELECT usesuper FROM pg_user WHERE usename=current_user)
            THEN (SELECT COALESCE(max(EXTRACT(EPOCH FROM now()-query_start)),0)::double precision
              FROM pg_stat_activity WHERE datname=current_database() AND state='active' AND pid<>pg_backend_pid()) END AS longest_query_seconds,
          stats_reset FROM pg_stat_database WHERE datname=current_database()
        """)[0]!.DeepClone().AsObject();

    public void Sample() => database.WriteNonLedger("compute:reaper", sql => sql.Execute("""
        INSERT INTO native_monitor_samples(captured_at,payload)
        VALUES(date_trunc('minute',now()),$1::jsonb) ON CONFLICT(captured_at) DO NOTHING
        """, new JsonObject { ["queue"] = Queue(sql), ["database"] = Database(sql), ["metrics"] = metrics.Snapshot() }.ToJsonString()));

    public JsonObject Clean() => database.WriteNonLedger("compute:reaper", sql =>
    {
        // Consumed previews are durable idempotency receipts. They must survive expiry.
        var previews = sql.Execute("""
            WITH old AS (SELECT user_id,id FROM review_sessions WHERE result IS NULL AND expires_at<now()-interval '1 day'
              ORDER BY expires_at LIMIT 1000 FOR UPDATE SKIP LOCKED)
            DELETE FROM review_sessions r USING old WHERE r.user_id=old.user_id AND r.id=old.id AND r.result IS NULL
            """);
        previews += sql.Execute("""
            WITH old AS (SELECT p.user_id,p.snapshot_id FROM agent_preview_snapshots p
              WHERE p.expires_at<now()-interval '1 day' AND NOT EXISTS(
                SELECT 1 FROM agent_research_attempts a WHERE a.user_id=p.user_id AND a.snapshot_id=p.snapshot_id)
              ORDER BY p.expires_at LIMIT $1 FOR UPDATE SKIP LOCKED)
            DELETE FROM agent_preview_snapshots p USING old WHERE p.user_id=old.user_id AND p.snapshot_id=old.snapshot_id
            """, 1000 - previews);
        // Only reproducible public market reads; financial/paid/replay/source jobs remain intact.
        var jobs = sql.Execute("""
            WITH old AS (SELECT user_id,job_id FROM compute_jobs
              WHERE status IN('completed','failed','cancelled','interrupted') AND completed_at<now()-interval '30 days'
              AND deadline<now() AND kind IN('market.snapshot','market.quotes','market.news','fund.detail','fund.valuations')
              ORDER BY completed_at LIMIT 1000 FOR UPDATE SKIP LOCKED)
            DELETE FROM compute_jobs j USING old WHERE j.user_id=old.user_id AND j.job_id=old.job_id
            """);
        var samples = sql.Execute("DELETE FROM native_monitor_samples WHERE captured_at<now()-interval '14 days'");
        sql.Execute("""
            INSERT INTO native_maintenance_state VALUES(1,now(),$1,$2,$3) ON CONFLICT(id) DO UPDATE SET
              completed_at=EXCLUDED.completed_at,previews_deleted=EXCLUDED.previews_deleted,
              public_jobs_deleted=EXCLUDED.public_jobs_deleted,samples_deleted=EXCLUDED.samples_deleted
            """, previews, jobs, samples);
        return new JsonObject { ["previews_deleted"] = previews, ["public_jobs_deleted"] = jobs, ["samples_deleted"] = samples };
    });

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (configuration["QUANT_DISABLE_MAINTENANCE"] == "true" || string.IsNullOrWhiteSpace(configuration["QUANT_SITE_ROOT"])) return;
        var nextCleanup = DateTimeOffset.MinValue;
        using var timer = new PeriodicTimer(TimeSpan.FromMinutes(1));
        try
        {
            do
            {
                try
                {
                    Sample();
                    if (DateTimeOffset.UtcNow >= nextCleanup) { Clean(); nextCleanup = DateTimeOffset.UtcNow.AddHours(1); }
                }
                catch (Exception error) when (error is Npgsql.NpgsqlException or IOException)
                { logger.LogError("Native maintenance failed ({ErrorType}); retrying next minute", error.GetType().Name); }
            } while (await timer.WaitForNextTickAsync(stoppingToken));
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
    }
}
