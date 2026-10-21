using QuantAssistant.Core.Abstractions;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Npgsql;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Jobs;

public sealed record ComputeJob(string UserId, string JobId, string Kind, string InputJson, int Attempt, string OwnerToken, DateTime Deadline, string? TraceId = null, string Priority = "normal");
public sealed record ComputeResult(string Status, string? ResultJson, string? ErrorCode, DateTime? CompletedAt, double? QueueMilliseconds = null, JsonNode? Timings = null);
public sealed record CachedPublicResult(string ResultJson, bool Fresh, DateTime CompletedAt);

/// <summary>Durable internal compute protocol. Workers never receive SQL credentials.</summary>
public sealed class ComputeJobRepository(string connectionString)
{
    // These operations read public sources and never receive an account snapshot.
    public static readonly string[] IndependentPublicKinds = ["market.snapshot", "market.quotes", "market.news", "market.history", "fund.detail", "fund.valuations", "fund.catalog"];
    public static int PublicCacheSeconds(string kind) => kind switch
    { "market.snapshot" => 30, "market.quotes" or "fund.valuations" or "market.news" => 60, "fund.detail" => 300, "market.history" => 3600, "fund.catalog" => 21600, _ => 0 };
    public static readonly HashSet<string> AllowedKinds = new(StringComparer.Ordinal)
    {
        "market.snapshot", "market.quotes", "market.news", "market.history", "fund.detail", "fund.valuations", "fund.catalog",
        "analysis.data_status", "analysis.decision", "analysis.stocks", "analysis.funds", "analysis.backtest",
        "analysis.lifecycle", "analysis.style_drift", "analysis.valuation_alerts", "analysis.profit", "analysis.benchmark", "analysis.price_dates",
        "strategy.view", "strategy.preview", "advisor.report", "advisor.preflight", "agent.research", "fund.execution_replay",
        "analysis.explain", "analysis.readiness", "fund.tracking", "fund.risk", "fund.public_observations", "fund.study", "fund.curves", "fund.study_update",
        "photo.recognize", "photo.identify",
        "fund.nav_sources",
        "ledger.replay", "analysis.brinson",
    };

    /// <summary>batch3 #11：任务优先级，critical &gt; high &gt; normal。未知值归一化为 normal。</summary>
    public static readonly string[] JobPriorities = ["critical", "high", "normal"];
    public static string NormalizePriority(string? priority) => priority switch
    {
        "critical" => "critical",
        "high" => "high",
        _ => "normal",
    };

    public static string EnqueueOwned(IAccountSql sql, string userId, string kind, string inputJson, DateTime deadline, string priority = "normal")
    {
        if (!AllowedKinds.Contains(kind)) throw new ArgumentException("计算任务类型不受支持");
        priority = NormalizePriority(priority);
        var id = Guid.NewGuid().ToString("N");
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(kind + ":" + inputJson))).ToLowerInvariant();
        sql.Execute("INSERT INTO compute_jobs(user_id,job_id,kind,input_json,input_hash,status,deadline,trace_id,priority) VALUES($1,$2,$3,$4::jsonb,$5,'queued',$6,$7,$8)", userId,id,kind,inputJson,hash,deadline,PerformanceTrace.Current?.TraceId,priority);
        return id;
    }

    public CachedPublicResult? ReadPublicCache(string userId, string kind, string inputJson) => ReadPublicCacheAsync(userId, kind, inputJson).GetAwaiter().GetResult();

    public async Task<CachedPublicResult?> ReadPublicCacheAsync(string userId, string kind, string inputJson, CancellationToken cancellation = default)
    {
        using var timing = PerformanceTrace.Measure("db");
        var maxAge = kind switch { "fund.detail" or "market.history" or "fund.catalog" => 7 * 86400, "market.snapshot" => 86400, _ => 0 };
        if (maxAge == 0) throw new ArgumentException("该任务不支持公开资料缓存");
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(kind + ":" + inputJson))).ToLowerInvariant();
        await using var conn = await OpenAsync(cancellation);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT result_json::text,completed_at>now()-make_interval(secs=>$5),completed_at FROM compute_jobs
            WHERE user_id=$1 AND kind=$2 AND input_hash=$3 AND status='completed' AND result_json IS NOT NULL
              AND completed_at>now()-make_interval(secs=>$4)
              AND EXISTS(SELECT 1 FROM users WHERE id=$1 AND status='active')
            ORDER BY completed_at DESC LIMIT 1
            """;
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(kind);
        cmd.Parameters.AddWithValue(hash);
        cmd.Parameters.AddWithValue(maxAge);
        cmd.Parameters.AddWithValue(PublicCacheSeconds(kind));
        await using var reader = await cmd.ExecuteReaderAsync(cancellation);
        return await reader.ReadAsync(cancellation) ? new(reader.GetString(0), reader.GetBoolean(1), reader.GetDateTime(2)) : null;
    }

    public string Enqueue(string userId, string kind, string inputJson, DateTime deadline, bool reuseCompleted = true) => EnqueueAsync(userId, kind, inputJson, deadline, reuseCompleted).GetAwaiter().GetResult();

    public async Task<string> EnqueueAsync(string userId, string kind, string inputJson, DateTime deadline, bool reuseCompleted = true, CancellationToken cancellation = default, int pendingLimit=20, string priority = "normal")
    {
        using var timing = PerformanceTrace.Measure("db");
        if (!AllowedKinds.Contains(kind)) throw new ArgumentException("计算任务类型不受支持");
        if(pendingLimit is <1 or >20) throw new ArgumentException("排队上限无效");
        using var parsed = JsonDocument.Parse(inputJson);
        if (parsed.RootElement.ValueKind != JsonValueKind.Object) throw new ArgumentException("计算输入无效");
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(kind + ":" + inputJson))).ToLowerInvariant();
        await using var conn = await OpenAsync(cancellation);
        await using var tx = await conn.BeginTransactionAsync(cancellation);
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "SELECT pg_advisory_xact_lock(hashtextextended($1,0))";
        cmd.Parameters.AddWithValue("compute:" + userId);
        await cmd.ExecuteNonQueryAsync(cancellation);
        cmd.Parameters.Clear();
        cmd.CommandText = """
            SELECT job_id FROM compute_jobs WHERE user_id=$1 AND kind=$2 AND input_hash=$3
              AND ((status IN ('queued','running') AND deadline>now()) OR
                (status='completed' AND $4 AND CASE WHEN $5>0
                  THEN completed_at>now()-make_interval(secs=>$5) ELSE deadline>now() END
                  AND (kind<>'fund.catalog' OR EXISTS(SELECT 1 FROM public_compute_snapshots p
                    WHERE p.kind=compute_jobs.kind AND p.input_hash=compute_jobs.input_hash
                      AND p.completed_at=compute_jobs.completed_at AND p.expires_at>now()))))
            ORDER BY created_at DESC LIMIT 1
            """;
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(kind);
        cmd.Parameters.AddWithValue(hash);
        cmd.Parameters.AddWithValue(reuseCompleted);
        cmd.Parameters.AddWithValue(PublicCacheSeconds(kind));
        if (await cmd.ExecuteScalarAsync(cancellation) is string existing) { await tx.CommitAsync(cancellation); return existing; }
        cmd.Parameters.Clear();
        cmd.CommandText = "SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND status IN ('queued','running','cancel_requested')";
        cmd.Parameters.AddWithValue(userId);
        if ((long)(await cmd.ExecuteScalarAsync(cancellation))! >= pendingLimit) throw new JobQuotaExceeded();
        var id = Guid.NewGuid().ToString("N");
        // Refresh the provider too, while retaining the original public-query hash.
        if (!reuseCompleted && IndependentPublicKinds.Contains(kind))
        {
            var refreshed = JsonNode.Parse(inputJson)!.AsObject();
            refreshed["_refresh"] = true;
            inputJson = refreshed.ToJsonString();
        }
        cmd.Parameters.Clear();
        priority = NormalizePriority(priority);
        cmd.CommandText = "INSERT INTO compute_jobs(user_id,job_id,kind,input_json,input_hash,status,deadline,trace_id,priority) VALUES($1,$2,$3,$4::jsonb,$5,'queued',$6,$7,$8)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(id);
        cmd.Parameters.AddWithValue(kind);
        cmd.Parameters.AddWithValue(inputJson);
        cmd.Parameters.AddWithValue(hash);
        cmd.Parameters.AddWithValue(deadline);
        cmd.Parameters.AddWithValue((object?)PerformanceTrace.Current?.TraceId ?? DBNull.Value);
        cmd.Parameters.AddWithValue(priority);
        await cmd.ExecuteNonQueryAsync(cancellation);
        await tx.CommitAsync(cancellation);
        ComputeJobNotifications.Local.PublishQueue();
        return id;
    }

    public ComputeJob? Claim(string workerId, string[] capabilities) => ClaimAsync(workerId, capabilities).GetAwaiter().GetResult();

    public async Task<ComputeJob?> ClaimAsync(string workerId, string[] capabilities, CancellationToken cancellation = default, int maxLowWaitMinutes = 120)
    {
        using var timing = PerformanceTrace.Measure("db");
        if (workerId.Length is < 1 or > 64 || capabilities.Length is < 1 or > 50 || capabilities.Any(kind => !AllowedKinds.Contains(kind))) throw new ArgumentException("Worker标识或能力无效");
        await using var conn = await OpenAsync(cancellation);
        await using var tx = await conn.BeginTransactionAsync(cancellation);
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "SELECT pg_advisory_xact_lock(hashtextextended('compute:claim',0))";
        await cmd.ExecuteNonQueryAsync(cancellation);
        var sql = new AccountSql(conn,tx,cancellation);
        await sql.ExecuteAsync("INSERT INTO worker_heartbeats(worker_id,capabilities,last_seen_at,stopping) VALUES($1,$2::jsonb,now(),false) ON CONFLICT(worker_id) DO UPDATE SET capabilities=EXCLUDED.capabilities,last_seen_at=now(),stopping=false",workerId,JsonSerializer.Serialize(capabilities));
        await ExpireAsync(sql);
        cmd.CommandText = "SELECT count(*) FROM compute_jobs WHERE status IN ('running','cancel_requested')";
        if ((long)(await cmd.ExecuteScalarAsync(cancellation))! >= 3) { await tx.CommitAsync(cancellation); return null; }
        // batch3 #11 starvation 防护：normal 任务排队超 maxLowWaitMinutes 分钟自动提级为 high，每任务只提级一次。
        cmd.CommandText = "UPDATE compute_jobs SET priority='high',priority_boosted=TRUE WHERE status='queued' AND priority='normal' AND NOT priority_boosted AND created_at<now()-make_interval(mins=>$1)";
        cmd.Parameters.AddWithValue((double)Math.Max(1, maxLowWaitMinutes));
        await cmd.ExecuteNonQueryAsync(cancellation);
        cmd.Parameters.Clear();
        cmd.CommandText = """
            SELECT j.user_id,j.job_id,j.kind,j.input_json::text,j.attempt,j.deadline,j.trace_id,j.priority
            FROM compute_jobs j JOIN users u ON u.id=j.user_id
            WHERE j.status='queued' AND u.status='active' AND j.kind=ANY($1)
              AND (j.kind=ANY($2) OR NOT EXISTS(SELECT 1 FROM compute_jobs active WHERE active.user_id=j.user_id
                AND active.status IN ('running','cancel_requested')))
            ORDER BY CASE j.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                     CASE WHEN j.kind='market.snapshot' THEN 0 ELSE 1 END,
                     j.created_at,j.job_id LIMIT 1 FOR UPDATE OF j SKIP LOCKED
            """;
        cmd.Parameters.AddWithValue(capabilities);
        cmd.Parameters.AddWithValue(IndependentPublicKinds);
        ComputeJob? job = null;
        await using (var reader = await cmd.ExecuteReaderAsync(cancellation))
            if (await reader.ReadAsync(cancellation)) job = new(reader.GetString(0), reader.GetString(1), reader.GetString(2), reader.GetString(3), reader.GetInt32(4), Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant(), reader.GetDateTime(5), reader.IsDBNull(6) ? null : reader.GetString(6), reader.GetString(7));
        if (job is not null)
        {
            cmd.Parameters.Clear();
            cmd.CommandText = "UPDATE compute_jobs SET status='running',owner_token=$3,worker_id=$4,lease_expires_at=least(deadline,now()+interval '30 seconds') WHERE user_id=$1 AND job_id=$2";
            cmd.Parameters.AddWithValue(job.UserId);
            cmd.Parameters.AddWithValue(job.JobId);
            cmd.Parameters.AddWithValue(job.OwnerToken);
            cmd.Parameters.AddWithValue(workerId);
            await cmd.ExecuteNonQueryAsync(cancellation);
            await ComputePublication.StartedAsync(sql,job.UserId,job.Kind,job.InputJson);
        }
        cmd.Parameters.Clear();
        cmd.CommandText = "INSERT INTO worker_heartbeats(worker_id,capabilities,last_seen_at,stopping) VALUES($1,$2::jsonb,now(),false) ON CONFLICT(worker_id) DO UPDATE SET capabilities=EXCLUDED.capabilities,last_seen_at=now(),stopping=false";
        cmd.Parameters.AddWithValue(workerId);
        cmd.Parameters.AddWithValue(JsonSerializer.Serialize(capabilities));
        await cmd.ExecuteNonQueryAsync(cancellation);
        await tx.CommitAsync(cancellation);
        return job;
    }

    public bool Heartbeat(string userId, string jobId, int attempt, string ownerToken) => HeartbeatAsync(userId, jobId, attempt, ownerToken).GetAwaiter().GetResult();

    public async Task<bool> HeartbeatAsync(string userId, string jobId, int attempt, string ownerToken, CancellationToken cancellation = default)
    {
        using var timing = PerformanceTrace.Measure("db");
        await using var conn = await OpenAsync(cancellation);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            UPDATE compute_jobs j SET lease_expires_at=least(deadline,now()+interval '30 seconds')
            WHERE user_id=$1 AND job_id=$2 AND attempt=$3 AND owner_token=$4 AND status='running'
              AND deadline>now() AND lease_expires_at>now()
              AND EXISTS(SELECT 1 FROM users WHERE id=j.user_id AND status='active')
            """;
        BindLease(cmd, userId, jobId, attempt, ownerToken);
        var active = await cmd.ExecuteNonQueryAsync(cancellation) == 1;
        if (active)
        {
            cmd.CommandText = "UPDATE worker_heartbeats SET last_seen_at=now() WHERE worker_id=(SELECT worker_id FROM compute_jobs WHERE user_id=$1 AND job_id=$2 AND attempt=$3 AND owner_token=$4)";
            await cmd.ExecuteNonQueryAsync(cancellation);
        }
        return active;
    }

    public bool Finish(string userId, string jobId, int attempt, string ownerToken, string? resultJson, string? errorCode) => FinishAsync(userId, jobId, attempt, ownerToken, resultJson, errorCode).GetAwaiter().GetResult();

    public async Task<bool> FinishAsync(string userId, string jobId, int attempt, string ownerToken, string? resultJson, string? errorCode, CancellationToken cancellation = default, JsonObject? timings = null)
    {
        using var timing = PerformanceTrace.Measure("db");
        if ((resultJson is null) == (errorCode is null)) throw new ArgumentException("计算结果与错误码必须提供一项");
        if (resultJson is not null) { using var _ = JsonDocument.Parse(resultJson); }
        if (errorCode?.Length > 100) throw new ArgumentException("错误码无效");
        await using var conn = await OpenAsync(cancellation);
        await using var tx = await conn.BeginTransactionAsync(cancellation);
        var sql = new AccountSql(conn,tx,cancellation);
        await sql.ExecuteAsync("SELECT pg_advisory_xact_lock(hashtextextended($1,0))","ledger:"+userId);
        await using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            UPDATE compute_jobs j SET status=CASE WHEN status='cancel_requested' THEN 'cancelled' WHEN $6::text IS NULL THEN 'completed' ELSE 'failed' END,
                result_json=CASE WHEN status='cancel_requested' THEN NULL ELSE $5::jsonb END,
                error_code=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE $6::text END,completed_at=now(),timings_json=$7::jsonb
            WHERE user_id=$1 AND job_id=$2 AND attempt=$3 AND owner_token=$4 AND status IN ('running','cancel_requested')
                AND deadline>now() AND lease_expires_at>now()
                AND EXISTS(SELECT 1 FROM users WHERE id=j.user_id AND status='active')
            RETURNING kind,input_json::text,status,error_code
            """;
        BindLease(cmd, userId, jobId, attempt, ownerToken);
        cmd.Parameters.AddWithValue((object?)resultJson ?? DBNull.Value);
        cmd.Parameters.AddWithValue((object?)errorCode ?? DBNull.Value);
        cmd.Parameters.AddWithValue((object?)timings?.ToJsonString() ?? DBNull.Value);
        string? kind=null,input=null,status=null,error=null;
        using(var reader=await cmd.ExecuteReaderAsync(cancellation)) if(await reader.ReadAsync(cancellation)) {kind=reader.GetString(0);input=reader.GetString(1);status=reader.GetString(2);error=reader.IsDBNull(3)?null:reader.GetString(3);}
        if(kind is null) return false;
        await ComputePublication.FinishedAsync(sql,userId,jobId,kind,input!,status!,status=="cancelled"?null:resultJson,error);
        await tx.CommitAsync(cancellation);
        ComputeJobNotifications.Local.Publish(userId, jobId);
        ComputeJobNotifications.Local.PublishQueue();
        return true;
    }

    public void Recover() => RecoverAsync().GetAwaiter().GetResult();
    public Task<int> RecoverAsync(CancellationToken cancellation = default) => new AccountDatabase(connectionString).WriteAsync("compute:reaper",async sql=>{await ExpireAsync(sql);return 0;}, cancellation);
    private static async Task ExpireAsync(IAccountSql sql)
    {
        var expired=await sql.QueryAsync("UPDATE compute_jobs j SET status='interrupted',error_code='worker_interrupted',completed_at=now() WHERE status IN('queued','running','cancel_requested') AND (deadline<=now() OR (status<>'queued' AND lease_expires_at<=now()) OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=j.user_id AND u.status='active')) RETURNING user_id,job_id,kind,input_json",Array.Empty<object?>());
        foreach(var row in expired.OfType<System.Text.Json.Nodes.JsonObject>()) await ComputePublication.FinishedAsync(sql,row["user_id"]!.GetValue<string>(),row["job_id"]!.GetValue<string>(),row["kind"]!.GetValue<string>(),row["input_json"]!.ToJsonString(),"interrupted",null,"interrupted");
    }

    public ComputeResult Read(string userId, string jobId) => ReadAsync(userId, jobId).GetAwaiter().GetResult();

    public async Task<ComputeResult> ReadAsync(string userId, string jobId, CancellationToken cancellation = default)
    {
        using var timing = PerformanceTrace.Measure("db");
        await using var conn = await OpenAsync(cancellation);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT status,result_json::text,error_code,completed_at,(extract(epoch FROM (claimed_at-created_at))*1000)::double precision,timings_json::text FROM compute_jobs WHERE user_id=$1 AND job_id=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(jobId);
        await using var reader = await cmd.ExecuteReaderAsync(cancellation);
        if (!await reader.ReadAsync(cancellation)) throw new JobNotFoundError(jobId);
        return new(reader.GetString(0), reader.IsDBNull(1) ? null : reader.GetString(1), reader.IsDBNull(2) ? null : reader.GetString(2), reader.IsDBNull(3) ? null : reader.GetDateTime(3), reader.IsDBNull(4) ? null : reader.GetDouble(4), reader.IsDBNull(5) ? null : JsonNode.Parse(reader.GetString(5)));
    }

    private static void BindLease(NpgsqlCommand command, string userId, string jobId, int attempt, string token)
    {
        command.Parameters.AddWithValue(userId); command.Parameters.AddWithValue(jobId);
        command.Parameters.AddWithValue(attempt); command.Parameters.AddWithValue(token);
    }

    private async Task<NpgsqlConnection> OpenAsync(CancellationToken cancellation)
    {
        var connection = new NpgsqlConnection(connectionString);
        try { await connection.OpenAsync(cancellation); return connection; }
        catch { await connection.DisposeAsync(); throw; }
    }
}
