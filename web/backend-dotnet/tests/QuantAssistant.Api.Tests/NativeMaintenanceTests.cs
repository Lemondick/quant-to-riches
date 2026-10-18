using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class NativeMaintenanceTests
{
    [Fact]
    public void P95UsesBoundedPublicWindow_ExcludesWorkerPolling()
    {
        var metrics = new NativeMetrics();
        for (var i = 1; i <= 100; i++) metrics.Observe(200, i);
        metrics.Observe(500, 90000, false);
        var result = metrics.Snapshot();
        Assert.Equal(95, result["p95_duration_ms"]!.GetValue<long>());
        Assert.Equal(100, result["duration_sample_count"]!.GetValue<int>());
        Assert.Equal(1, result["server_errors"]!.GetValue<long>());
        for (var i = 0; i < 5000; i++) metrics.Observe(200, 7);
        Assert.Equal(4096, metrics.Snapshot()["duration_sample_count"]!.GetValue<int>());
        Assert.Contains("quant_http_recent_p95_milliseconds 7", metrics.Render());
        Assert.Null(new NativeMetrics().Snapshot()["p95_duration_ms"]);
    }

    [PgFact]
    public void CleanupPreservesReceiptsFinancialJobsAndLiveJobs_AndIsRepeatable()
    {
        using var user = new PgTestUser();
        var db = new AccountDatabase(user.Connection);
        var service = new NativeRuntimeMaintenance(db, new NativeMetrics(), new ConfigurationBuilder().Build(), NullLogger<NativeRuntimeMaintenance>.Instance);
        try
        {
            user.Execute("INSERT INTO review_sessions(user_id,id,kind,payload,expires_at,result) VALUES($1,'expired','photo','{}',now()-interval '3 days',null),($1,'receipt','photo','{}',now()-interval '3 days','{\"accepted\":true}'),($1,'live','photo','{}',now()+interval '1 day',null)", user.Id);
            user.Execute("INSERT INTO agent_preview_snapshots(user_id,snapshot_id,content_hash,payload_json,created_at,expires_at) VALUES($1,'unused','x','{}',now()-interval '4 days',now()-interval '3 days'),($1,'used','x','{}',now()-interval '4 days',now()-interval '3 days')", user.Id);
            user.Execute("INSERT INTO agent_research_attempts(user_id,job_id,attempt_no,snapshot_id,quota_date,started_at,execution_status,provider,model,prompt_version,upstream_commit) VALUES($1,'history',1,'used',current_date,now(),'completed','compatible','synthetic','v1','test')", user.Id);
            foreach (var (id, kind, status, days) in new[] { ("old-public", "market.quotes", "completed", 40), ("old-paid", "advisor.report", "failed", 40), ("old-replay", "ledger.replay", "completed", 40), ("new-public", "fund.detail", "completed", 1), ("live-public", "market.quotes", "running", 40) })
                user.Execute("INSERT INTO compute_jobs(user_id,job_id,kind,input_json,input_hash,status,deadline,completed_at) VALUES($1,$2,$3,'{}','x',$4,now()-interval '50 days',now()-$5*interval '1 day')", user.Id, id, kind, status, days);
            service.Clean();
            Assert.Equal(2L, user.Query("SELECT count(*) FROM review_sessions WHERE user_id=$1", user.Id));
            Assert.Equal(4L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
            Assert.Equal(1L, user.Query("SELECT count(*) FROM agent_preview_snapshots WHERE user_id=$1 AND snapshot_id='used'", user.Id));
            Assert.Equal(0L, user.Query("SELECT count(*) FROM agent_preview_snapshots WHERE user_id=$1 AND snapshot_id='unused'", user.Id));
            Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND job_id='old-public'", user.Id));
            Assert.Equal(0, service.Clean()["public_jobs_deleted"]!.GetValue<int>());
            service.Sample(); service.Sample();
            var snapshot = db.Read(sql => NativeRuntimeMaintenance.Database(sql));
            Assert.True(snapshot["connections"]!.GetValue<int>() > 0);
            Assert.True(snapshot["blocked_connections"]!.GetValue<long>() >= 0);
            Assert.DoesNotContain("query", snapshot.ToJsonString().Replace("longest_query_seconds", ""));
            Assert.Equal(0L, user.Query("SELECT count(*) FROM transactions WHERE user_id=$1", user.Id));
        }
        finally { user.Execute("DELETE FROM review_sessions WHERE user_id=$1", user.Id); user.Execute("DELETE FROM agent_preview_snapshots WHERE user_id=$1", user.Id); }
    }
}
