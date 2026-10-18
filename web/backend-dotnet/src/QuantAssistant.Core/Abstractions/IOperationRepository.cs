using QuantAssistant.Core.Errors;

namespace QuantAssistant.Core.Abstractions;

/// <summary>
/// 运行协调仓储：租约锁（operation_locks）、任务心跳（job_heartbeats）。
/// 实现见 Infrastructure.Operations.OperationRepository。
/// </summary>
public interface IOperationRepository
{
    /// <summary>删除过期锁并尝试插入新锁；锁名冲突抛 OperationAlreadyRunningError（对应 operation_already_running）。</summary>
    void AcquireLock(string userId, string lockName, string ownerToken, DateTime acquiredAt, DateTime expiresAt);

    /// <summary>释放指定 owner 持有的锁（幂等）。</summary>
    void ReleaseLock(string userId, string lockName, string ownerToken);

    /// <summary>更新任务心跳（upsert）。</summary>
    void UpsertHeartbeat(string userId, string jobName, int expectedIntervalMinutes, DateTime startedAt, DateTime completedAt, string status, string? error);
}

/// <summary>锁名冲突：同一运行已被其他 owner 持有（映射 operation_already_running）。</summary>
public sealed class OperationAlreadyRunningError(string lockName) : Exception($"operation_already_running:{lockName}");