using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Infrastructure.Jobs;

namespace QuantAssistant.Api.Services;

/// <summary>Account-facing access to the three resumable, non-model computations.</summary>
public sealed class UserComputeTasks(ComputeJobRepository jobs, IAccountDatabase database, IConfiguration configuration)
{
    private static readonly string[] Kinds = ["portfolio.backtest", "valuation.percentiles", "dca.smart_plan_backtest"];

    public async Task<JsonObject> StartAsync(string userId, string kind, JsonNode input, CancellationToken cancellation)
    {
        var seconds = kind switch
        {
            "portfolio.backtest" => 600, "valuation.percentiles" => 180, "dca.smart_plan_backtest" => 300,
            _ => throw new ArgumentException("该计算不支持后台查询。"),
        };
        if (configuration["QUANT_WORKER_SECRET"] is not { Length: >= 32 })
            throw new ApplicationFailure("worker_unavailable", 503, "计算进程尚未配置。");
        var id = await jobs.EnqueueAsync(userId, kind, input.ToJsonString(), DateTime.UtcNow.AddSeconds(seconds),
            cancellation: cancellation, priority: "high");
        return await ReadAsync(userId, id, cancellation);
    }

    public async Task<JsonObject> ReadAsync(string userId, string id, CancellationToken cancellation)
    {
        var rows = await database.QueryAsync("""
            SELECT job_id,kind,status,created_at,deadline,completed_at,
                CASE WHEN status='completed' THEN result_json ELSE NULL END AS result,error_code,
                status IN ('queued','running','cancel_requested') AND
                    (deadline<=now() OR (status<>'queued' AND (lease_expires_at IS NULL OR lease_expires_at<=now()))) AS expired
            FROM compute_jobs WHERE user_id=$1 AND job_id=$2 AND kind=ANY($3)
            """, [userId, id, Kinds], cancellation);
        if (rows.FirstOrDefault() is not JsonObject row) throw new JobNotFoundError(id);
        if (row["expired"]!.GetValue<bool>())
        {
            row["status"] = "interrupted";
            row["result"] = null;
            row["error_code"] = "worker_interrupted";
        }
        row.Remove("expired");
        if (row["error_code"] is JsonValue error && error.GetValue<string>() is not ("invalid_request" or "worker_interrupted" or "cancelled"))
            row["error_code"] = "worker_unavailable";
        return row;
    }

    public async Task<JsonObject> CancelAsync(string userId, string id, CancellationToken cancellation)
    {
        var changed = await database.WriteNonLedgerAsync(userId, sql => sql.ExecuteAsync("""
            UPDATE compute_jobs SET status='cancelled',result_json=NULL,error_code='cancelled',completed_at=now()
            WHERE user_id=$1 AND job_id=$2 AND kind=ANY($3)
                AND status IN ('queued','running','cancel_requested') AND deadline>now()
                AND (status='queued' OR lease_expires_at>now())
            """, userId, id, Kinds), cancellation);
        if (changed > 0)
        {
            ComputeJobNotifications.Local.Publish(userId, id);
            ComputeJobNotifications.Local.PublishQueue();
        }
        return await ReadAsync(userId, id, cancellation);
    }
}
