using QuantAssistant.Core.Auth;

namespace QuantAssistant.Core.Abstractions;

/// <summary>账号仓储契约（Core 只依赖接口；实现见 Infrastructure.Auth.AccountRepository）。</summary>
public interface IAccountRepository
{
    int CountUsers();
    int CountAdmins();
    bool RegistrationEnabled();
    void SetRegistrationEnabled(bool enabled, string actorId, DateTime now);
    void CreateUser(string id, string username, string passwordHash, AccountRole role, DateTime now);
    Account? FindByUsername(string username);
    Account? FindById(string userId);
    void CreateSession(string tokenHash, string sessionId, string userId, string csrf, string device, DateTime now, DateTime expires);
    (string UserId, string Csrf, DateTime Expires)? FindSession(string tokenHash);
    void RevokeSession(string tokenHash);
    void RevokeAllSessions(string userId);
    IReadOnlyList<AccountSession> ListSessions(string userId, DateTime now);
    bool RevokeOwnedSession(string userId, string sessionId);
    void RevokeOtherSessions(string userId, string currentTokenHash);
    bool ChangePassword(string userId, string currentHash, string replacementHash, DateTime now);
    void RecordUsage(string userId, string category, int status, long durationMs, DateTime now);
    Task<AuthSession?> ActiveSessionAsync(string tokenHash, DateTime now, CancellationToken cancellation = default);
    Task RecordUsageAsync(string userId, string category, int status, long durationMs, DateTime now, CancellationToken cancellation = default);
    Task RecordUsageBatchAsync(IReadOnlyList<UsageDelta> records, CancellationToken cancellation = default);
    void IssueRecoveryToken(string tokenHash, string userId, string issuedBy, DateTime now, DateTime expires);
    bool RedeemRecovery(string username, string tokenHash, string newPasswordHash, DateTime now);

    // 管理员操作（P2 账号域）
    IReadOnlyList<Account> ListUsers(string? search);
    bool SetUserStatus(string actorId, string userId, AccountStatus status, DateTime now);
    void AdminRevokeSessions(string actorId, string userId, DateTime now);
    (IReadOnlyList<AuditEntry> Items, int Total) ListAudit(int page, int limit);
}

public sealed record AuditEntry(string ActorId, string Action, string? TargetId, string Detail, DateTime CreatedAt);
public sealed record AccountSession(string SessionId, string Device, DateTime CreatedAt, DateTime ExpiresAt, string TokenHash);
public sealed record UsageDelta(string UserId, string Day, string Category, int Requests, int Errors, long DurationMs, DateTime LastActivityAt);
