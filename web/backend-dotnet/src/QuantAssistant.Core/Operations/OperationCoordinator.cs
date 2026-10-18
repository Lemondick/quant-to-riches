using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Core.Operations;

/// <summary>
/// 运行租约协调器（对应 Python OperationCoordinator.hold）。
/// 通过 operation_locks 表 + 唯一约束实现排他租约：删除过期锁 → 插入（冲突抛 OperationAlreadyRunningError）。
/// </summary>
public sealed class OperationCoordinator(IOperationRepository repository, TimeProvider timeProvider)
{
    /// <summary>获取排他租约，返回可释放的句柄；同锁名已被持有则抛 OperationAlreadyRunningError。</summary>
    public LeaseHold Hold(string userId, string lockName, TimeSpan lease)
    {
        if (string.IsNullOrWhiteSpace(lockName))
        {
            throw new ArgumentException("lock_name must be a non-empty string");
        }

        if (lease <= TimeSpan.Zero)
        {
            throw new ArgumentException("lease must be positive");
        }

        var owner = Guid.NewGuid().ToString("N");
        var acquiredAt = timeProvider.GetUtcNow().UtcDateTime;
        var expiresAt = acquiredAt + lease;
        repository.AcquireLock(userId, lockName.Trim(), owner, acquiredAt, expiresAt);
        return new LeaseHold(() => repository.ReleaseLock(userId, lockName.Trim(), owner), owner);
    }
}

/// <summary>租约句柄；释放时删除锁（幂等）。</summary>
public sealed class LeaseHold(Action release, string ownerToken) : IDisposable
{
    public string OwnerToken { get; } = ownerToken;

    public void Dispose() => release();
}
