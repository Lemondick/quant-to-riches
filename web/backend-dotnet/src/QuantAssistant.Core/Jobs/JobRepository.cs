using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Core.Jobs;

/// <summary>任务状态（agent_research_jobs.status）。</summary>
public static class JobStatus
{
    public const string Queued = "queued";
    public const string Running = "running";
    public const string Completed = "completed";
    public const string Failed = "failed";
    public const string Cancelled = "cancelled";
    public const string CancelRequested = "cancel_requested";
    public const string Interrupted = "interrupted";

    public static readonly string[] Active = [Queued, Running, CancelRequested];
}

/// <summary>研究任务视图。</summary>
public sealed record ResearchJob(string JobId, string CacheKey, string Symbol, string Status, int AttemptNo, string Phase);

/// <summary>研究任务详情（get 返回，含错误与报告内容）。</summary>
public sealed record ResearchJobDetail(
    string JobId,
    string Symbol,
    string Status,
    int AttemptNo,
    string? ErrorCode,
    string? ReportJson,
    string? UsageJson,
    DateTime CreatedAt,
    DateTime? CompletedAt);

/// <summary>任务仓储契约（P4）：入队、领取、结果提交、取消、重启恢复。实现见 Infrastructure.Jobs.JobRepository。</summary>
public interface IJobRepository
{
    /// <summary>幂等入队；返回 (jobId, 是否已存在)。配额超限抛 JobQuotaExceeded。</summary>
    (string JobId, bool Existing) Enqueue(string userId, string cacheKey, string symbol, string snapshotId, string provider, string model, DateOnly quotaDate, DateTime now);

    /// <summary>领取下一个 queued 任务并置 running（原子），返回任务或 null。</summary>
    ResearchJob? NextJob(string userId, DateTime deadline);

    /// <summary>幂等结果提交：仅当 attempt 匹配且状态为 active 才提交，旧 Worker 不能覆盖新状态。</summary>
    bool Finish(string userId, string jobId, int attempt, string status, string? error, string? reportJson, string? usageJson, DateTime now);

    /// <summary>取消任务：queued→cancelled，running→cancel_requested。</summary>
    void Cancel(string userId, string jobId, DateTime now);

    /// <summary>重启恢复：所有 queued/running/cancel_requested → interrupted。</summary>
    void Recover(string userId, DateTime now);

    /// <summary>按 job_id 查询任务详情（不存在抛 JobNotFoundError）。</summary>
    ResearchJobDetail Get(string userId, string jobId);
}

/// <summary>任务不存在（映射 404 job_not_found）。</summary>
public sealed class JobNotFoundError(string jobId) : Exception($"job_not_found:{jobId}");

/// <summary>配额超限（映射 429 quota_exceeded）。</summary>
public sealed class JobQuotaExceeded() : Exception("quota_exceeded");