using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Operations;

/// <summary>E5：任务心跳/运维视图仓储（原 OperationsViewsController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class JobHeartbeatRepository(IAccountDatabase database)
{
    public async Task RecordHeartbeatAsync(string userId, string jobName, int expectedIntervalMinutes, string status,
        DateTime? startedAt, DateTime? completedAt, string? error, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, sql => sql.ExecuteAsync("""
            INSERT INTO job_heartbeats(user_id,job_name,expected_interval_minutes,last_started_at,last_completed_at,last_status,last_error,updated_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,now()) ON CONFLICT(user_id,job_name) DO UPDATE SET
            expected_interval_minutes=EXCLUDED.expected_interval_minutes,
            last_started_at=coalesce(EXCLUDED.last_started_at,job_heartbeats.last_started_at),
            last_completed_at=coalesce(EXCLUDED.last_completed_at,job_heartbeats.last_completed_at),
            last_status=EXCLUDED.last_status,last_error=EXCLUDED.last_error,updated_at=now()
            """, userId, jobName, expectedIntervalMinutes, startedAt, completedAt, status, error), cancellation);

    public async Task<JsonArray> WatchdogAsync(string userId, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var rows = await sql.QueryAsync("""
                SELECT job_name,expected_interval_minutes,last_started_at,last_completed_at,last_status AS status,last_error,
                (last_completed_at IS NULL OR now()-last_completed_at>expected_interval_minutes*interval '1 minute') AS overdue,
                (last_status IN ('failed','blocked')) AS failed,
                (last_completed_at IS NOT NULL AND now()-last_completed_at<=expected_interval_minutes*interval '1 minute' AND last_status NOT IN ('failed','blocked')) AS healthy
                FROM job_heartbeats WHERE user_id=$1 ORDER BY job_name
                """, userId);
            foreach (var row in rows.OfType<JsonObject>())
            {
                if (!row["overdue"]!.GetValue<bool>() && !row["failed"]!.GetValue<bool>()) continue;
                var name = row["job_name"]!.GetValue<string>(); var failed = row["failed"]!.GetValue<bool>();
                await sql.ExecuteAsync("""
                    INSERT INTO operational_alerts(user_id,alert_key,severity,message,status,first_seen_at,last_seen_at,details_json)
                    VALUES($1,$2,$3,$4,'open',now(),now(),$5::json) ON CONFLICT(user_id,alert_key,status) DO UPDATE SET
                    severity=EXCLUDED.severity,message=EXCLUDED.message,last_seen_at=EXCLUDED.last_seen_at,details_json=EXCLUDED.details_json
                    """, userId, "scheduler:" + name, failed ? "critical" : "warning", $"任务 {name} {(failed ? "失败" : "超时未运行")}", row.ToJsonString());
            }
            return rows;
        }, cancellation);

    public async Task<JsonObject> TrendsAsync(string userId, int limit, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql => new JsonObject
        {
            ["daily"] = await sql.QueryAsync("""
                WITH r AS (SELECT as_of_date,count(*) AS decision_runs,count(*) FILTER(WHERE data_status='valid') AS valid_decisions,
                    count(*) FILTER(WHERE data_status='stale') AS stale_decisions,count(*) FILTER(WHERE data_status='blocked') AS blocked_decisions
                    FROM strategy_runs WHERE user_id=$1 GROUP BY as_of_date),
                s AS (SELECT as_of_date,count(*) AS snapshot_count,count(*) FILTER(WHERE status='valid') AS valid_snapshots,
                    count(DISTINCT source) AS source_count,max(fetched_at) AS latest_fetched_at,
                    greatest(0,max((fetched_at AT TIME ZONE 'Asia/Shanghai')::date-as_of_date)) AS fetch_lag_days FROM data_snapshots WHERE user_id=$1 GROUP BY as_of_date)
                SELECT * FROM (SELECT coalesce(r.as_of_date,s.as_of_date) AS as_of_date,coalesce(r.decision_runs,0) AS decision_runs,
                    coalesce(r.valid_decisions,0) AS valid_decisions,coalesce(r.stale_decisions,0) AS stale_decisions,coalesce(r.blocked_decisions,0) AS blocked_decisions,
                    r.valid_decisions::double precision/nullif(r.decision_runs,0) AS decision_pass_rate,coalesce(s.snapshot_count,0) AS snapshot_count,
                    coalesce(s.valid_snapshots,0) AS valid_snapshots,coalesce(s.source_count,0) AS source_count,
                    s.valid_snapshots::double precision/nullif(s.snapshot_count,0) AS snapshot_valid_rate,s.latest_fetched_at,s.fetch_lag_days
                    FROM r FULL JOIN s USING(as_of_date) ORDER BY as_of_date DESC LIMIT $2) recent ORDER BY as_of_date
                """, userId, limit),
            ["sources"] = await sql.QueryAsync("""
                SELECT * FROM (SELECT as_of_date,source,count(*) AS snapshot_count,count(*) FILTER(WHERE status='valid') AS valid_snapshots,
                    count(*) FILTER(WHERE status='valid')::double precision/count(*) AS valid_snapshot_rate,max(fetched_at) AS latest_fetched_at
                    FROM data_snapshots WHERE user_id=$1 GROUP BY as_of_date,source ORDER BY as_of_date DESC,source LIMIT $2) recent ORDER BY as_of_date,source
                """, userId, Math.Min(2000, limit * 10)),
        }, cancellation);
}
