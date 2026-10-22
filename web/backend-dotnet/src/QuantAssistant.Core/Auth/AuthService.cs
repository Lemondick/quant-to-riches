using System.Security.Cryptography;
using System.Text;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Core.Auth;

/// <summary>认证操作结果：成功或携带对外错误码。</summary>
public sealed record AuthResult(bool Ok, string? ErrorCode = null, Account? Account = null);

/// <summary>
/// 认证服务编排：注册、登录校验、会话、恢复码兑付。依赖 IAccountRepository（仓储）与 IPasswordHasher。
/// 会话令牌与旧站点一致：token = 随机 32 字节 base64url，token_hash = SHA256(token)（对应 token_digest）。
/// 会话 TTL 12 小时、恢复码 15 分钟，见 AuthErrors。
/// </summary>
public sealed class AuthService(
    IAccountRepository repository,
    IPasswordHasher hasher,
    TimeProvider timeProvider)
{
    private static readonly TimeSpan SessionTtl = TimeSpan.FromSeconds(AuthErrors.SessionSeconds);
    private static readonly TimeSpan RecoveryTtl = TimeSpan.FromSeconds(AuthErrors.RecoverySeconds);

    // 有效但无关的 Argon2id 哈希，用于账号不存在时仍执行一次校验以抵抗时序侧信道（旧站点 _dummy_hash 语义）。
    private const string DummyHash =
        "$argon2id$v=19$m=19456,t=2,p=1$reCWPtbUkOb3BknGGilWqA$z75BDPLASZRvXQivXEQ22B0NebSLN02Z/A9xGavqWd8";

    public Account InitializeAdmin(string username, string password)
    {
        if (!AccountRules.IsValidUsername(username)) throw new AuthFailure("invalid_username");
        if (!AccountRules.IsValidPassword(password)) throw new AuthFailure("invalid_password");
        var now=Now();
        var account = new Account(Guid.NewGuid().ToString("N"), username.Trim().ToLowerInvariant(), hasher.Hash(password), AccountRole.Admin, AccountStatus.Active,now);
        repository.CreateUser(account.Id, account.Username, account.PasswordHash, AccountRole.Admin, now);
        return account;
    }

    public object Sessions(string token)
    {
        var account = SessionAccount(token) ?? throw new AuthFailure("authentication_required");
        var hash = Sha256(token);
        return new { items = repository.ListSessions(account.Id, Now()).Select(row => new
        { session_id = row.SessionId, device = row.Device, created_at = row.CreatedAt, expires_at = row.ExpiresAt, current = row.TokenHash == hash }) };
    }

    public object RevokeSessions(string token, string? sessionId = null)
    {
        var account = SessionAccount(token) ?? throw new AuthFailure("authentication_required");
        var hash = Sha256(token);
        if (sessionId is null)
        {
            var revoked = repository.ListSessions(account.Id, Now()).Count(row => row.TokenHash != hash);
            repository.RevokeOtherSessions(account.Id, hash);
            return new { status = "sessions_revoked", current_revoked = false, revoked };
        }
        var current = repository.ListSessions(account.Id, Now()).Any(row => row.SessionId == sessionId && row.TokenHash == hash);
        if (!repository.RevokeOwnedSession(account.Id, sessionId)) throw new AuthFailure("session_not_found");
        return new { status = "sessions_revoked", current_revoked = current, revoked = 1 };
    }

    public void ChangePassword(string token, string currentPassword, string newPassword)
    {
        if (!AccountRules.IsValidPassword(newPassword)) throw new AuthFailure("invalid_password");
        var account = SessionAccount(token) ?? throw new AuthFailure("authentication_required");
        if (!hasher.Verify(account.PasswordHash, currentPassword)
            || !repository.ChangePassword(account.Id, account.PasswordHash, hasher.Hash(newPassword), Now()))
            throw new AuthFailure("invalid_credentials");
    }

    public AuthResult Register(string username, string password)
    {
        if (!AccountRules.IsValidUsername(username))
        {
            return new AuthResult(false, "invalid_username");
        }

        if (!AccountRules.IsValidPassword(password))
        {
            return new AuthResult(false, "invalid_password");
        }

        var normalized = username.Trim().ToLowerInvariant();
        if (repository.RegistrationEnabled() is false)
        {
            return new AuthResult(false, repository.CountAdmins() > 0
                ? "registration_paused" : "registration_closed");
        }

        if (repository.CountUsers() >= AccountRules.AccountLimit)
        {
            return new AuthResult(false, "registration_full");
        }

        try
        {
            var now=Now();
            var account = new Account(Guid.NewGuid().ToString("N"), normalized, hasher.Hash(password), AccountRole.User, AccountStatus.Active,now);
            repository.CreateUser(account.Id, normalized, account.PasswordHash, account.Role, now);
            return new AuthResult(true, Account: account);
        }
        catch (AccountConflict)
        {
            return new AuthResult(false, "username_taken");
        }
        catch (AuthFailure error)
        {
            return new AuthResult(false, error.Code);
        }
    }

    public Account? Authenticate(string username, string password)
    {
        if (username is null || password is null)
        {
            return null;
        }

        var account = repository.FindByUsername(username.Trim().ToLowerInvariant());
        if (account is null)
        {
            _ = hasher.Verify(DummyHash, password);
            return null;
        }

        return hasher.Verify(account.PasswordHash, password) && account.Status == AccountStatus.Active ? account : null;
    }

    public (string Token, string Csrf, DateTime ExpiresAt) CreateSession(string userId, string? device)
    {
        var token = GenerateToken();
        var csrf = GenerateToken();
        var now = Now();
        repository.CreateSession(Sha256(token), Guid.NewGuid().ToString("N"), userId, csrf, string.IsNullOrWhiteSpace(device) ? "未知设备" : device, now, now + SessionTtl);
        return (token, csrf, now + SessionTtl);
    }

    public Account? SessionAccount(string token) => SessionData(token)?.Account;

    public AuthSession? SessionData(string token)
    {
        var session = repository.FindSession(Sha256(token));
        if (session is null || session.Value.Expires <= Now())
        {
            return null;
        }

        var account = repository.FindById(session.Value.UserId);
        return account is { Status: AccountStatus.Active }
            ? new AuthSession(account, session.Value.Csrf, session.Value.Expires) : null;
    }
    public Task<AuthSession?> SessionDataAsync(string token, CancellationToken cancellation = default) => repository.ActiveSessionAsync(Sha256(token), Now(), cancellation);

    public string? SessionCsrf(string token)
    {
        return SessionData(token)?.Csrf;
    }

    public void RevokeSession(string token) => repository.RevokeSession(Sha256(token));

    public AuthResult RedeemRecovery(string username, string token, string newPassword)
    {
        if (!AccountRules.IsValidPassword(newPassword))
        {
            return new AuthResult(false, "invalid_password");
        }

        var normalized = username.Trim().ToLowerInvariant();
        var ok = repository.RedeemRecovery(normalized, Sha256(token), hasher.Hash(newPassword), Now());
        return ok ? new AuthResult(true) : new AuthResult(false, "invalid_recovery_token");
    }

    /// <summary>管理员签发恢复码，返回明文 token 与过期时间（仅此一次可见）。</summary>
    public (string Token, DateTime ExpiresAt) IssueRecovery(string userId, string issuedBy)
    {
        var token = GenerateToken();
        var now = Now();
        repository.IssueRecoveryToken(Sha256(token), userId, issuedBy, now, now + RecoveryTtl);
        return (token, now + RecoveryTtl);
    }

    private DateTime Now() => timeProvider.GetUtcNow().UtcDateTime;

    /// <summary>校验会话为活跃管理员，返回账号；否则 null（映射 admin_required / authentication_required）。</summary>
    public Account? RequireAdmin(string token)
    {
        var account = SessionAccount(token);
        return account is { Role: AccountRole.Admin, Status: AccountStatus.Active } ? account : null;
    }

    public IReadOnlyList<Account> ListUsers(string? search) => repository.ListUsers(search);

    public object RegistrationStatus()
    {
        var users=repository.ListUsers(null);var initialized=users.Any(u=>u.Role==AccountRole.Admin);var enabled=repository.RegistrationEnabled();
        return new {initialized,registered=users.Count,active=users.Count(u=>u.Status==AccountStatus.Active),registration_enabled=enabled,
            limit=AccountRules.AccountLimit,remaining=Math.Max(0,AccountRules.AccountLimit-users.Count),
            registration_open=initialized&&enabled&&users.Count<AccountRules.AccountLimit,authentication="session"};
    }

    public AuthResult SetUserStatus(string actorId, string userId, AccountStatus status)
    {
        try
        {
            repository.SetUserStatus(actorId, userId, status, Now());
            return new AuthResult(true);
        }
        catch (AccountNotFound)
        {
            return new AuthResult(false, "account_not_found");
        }
        catch (AccountConflict)
        {
            return new AuthResult(false, "admin_protected");
        }
    }

    public void AdminRevokeSessions(string actorId, string userId) => repository.AdminRevokeSessions(actorId, userId, Now());

    /// <summary>F6：管理者在 user ↔ readonly 之间调整用户角色。</summary>
    public AuthResult SetUserRole(string actorId, string userId, AccountRole role)
    {
        try
        {
            repository.SetUserRole(actorId, userId, role, Now());
            return new AuthResult(true);
        }
        catch (AccountNotFound)
        {
            return new AuthResult(false, "account_not_found");
        }
        catch (AccountConflict)
        {
            return new AuthResult(false, "admin_protected");
        }
        catch (ArgumentException)
        {
            return new AuthResult(false, "invalid_request");
        }
    }

    public void SetRegistrationEnabled(string actorId, bool enabled) => repository.SetRegistrationEnabled(enabled, actorId, Now());

    public (IReadOnlyList<AuditEntry> Items, int Total) ListAudit(int page, int limit) => repository.ListAudit(page, limit);

    // ===== batch2-11：双重验证（TOTP RFC6238 + 恢复码）=====

    public (string? Secret, bool Enabled) TotpState(string userId) => repository.GetTotp(userId);

    /// <summary>登录第一步通过后：签发 2FA 挑战（5 分钟有效，单次使用）。返回明文 pending token。</summary>
    public string BeginTotpChallenge(string userId)
    {
        var token = GenerateToken();
        var now = Now();
        repository.CreateTotpChallenge(Sha256(token), userId, now, now.AddMinutes(5));
        return token;
    }

    /// <summary>登录第二步：消费挑战并校验 6 位 TOTP 码或恢复码；通过返回账号，否则 null。</summary>
    public Account? VerifyTotpChallenge(string pendingToken, string code)
    {
        if (string.IsNullOrWhiteSpace(pendingToken)) return null;
        var userId = repository.ConsumeTotpChallenge(Sha256(pendingToken.Trim()), Now());
        if (userId is null) return null;
        var account = repository.FindById(userId);
        if (account is not { Status: AccountStatus.Active }) return null;
        var (secret, enabled) = repository.GetTotp(userId);
        if (!enabled || string.IsNullOrEmpty(secret)) return account; // 未启用→直接通过（防御性）
        var normalized = code?.Trim() ?? "";
        if (Totp.Verify(secret, normalized, DateTimeOffset.UtcNow)) return account;
        foreach (var (id, hash) in repository.ListUnusedRecoveryCodes(userId))
        {
            if (hasher.Verify(hash, normalized.ToUpperInvariant()))
            {
                return repository.MarkRecoveryCodeUsed(id) ? account : null;
            }
        }
        return null;
    }

    /// <summary>开始启用：生成密钥并存储（尚未启用），返回密钥与扫码 URI。</summary>
    public (string Secret, string ProvisioningUri) BeginTotpEnrollment(string userId, string issuer)
    {
        var account = repository.FindById(userId) ?? throw new AuthFailure("account_not_found");
        var secret = Totp.GenerateSecret();
        repository.SetTotpSecret(userId, secret);
        return (secret, Totp.ProvisioningUri(issuer, account.Username, secret));
    }

    /// <summary>确认启用：校验动态口令，通过则启用并签发 10 个恢复码（明文仅返回一次）。</summary>
    public IReadOnlyList<string> ConfirmTotpEnrollment(string userId, string code)
    {
        var (secret, enabled) = repository.GetTotp(userId);
        if (enabled) throw new ApplicationFailure("invalid_request", 409, "双重验证已启用，无需重复确认。");
        if (string.IsNullOrEmpty(secret) || !Totp.Verify(secret, code?.Trim() ?? "", DateTimeOffset.UtcNow))
            throw new AuthFailure("invalid_totp_code");
        repository.SetTotpEnabled(userId, true);
        return RegenerateRecoveryCodes(userId);
    }

    /// <summary>重新生成恢复码（作废旧码，明文仅返回一次）。</summary>
    public IReadOnlyList<string> RegenerateRecoveryCodes(string userId)
    {
        var (_, enabled) = repository.GetTotp(userId);
        if (!enabled) throw new ApplicationFailure("invalid_request", 409, "请先启用双重验证。");
        var codes = Enumerable.Range(0, 10).Select(_ => GenerateRecoveryCode()).ToArray();
        repository.ReplaceRecoveryCodes(userId, codes.Select(hasher.Hash).ToArray(), Now());
        return codes;
    }

    /// <summary>关闭 2FA：需先验证密码；清除密钥与恢复码。</summary>
    public void DisableTotp(string userId, string password)
    {
        var account = repository.FindById(userId) ?? throw new AuthFailure("account_not_found");
        if (!hasher.Verify(account.PasswordHash, password ?? "")) throw new AuthFailure("invalid_credentials");
        repository.ClearTotp(userId);
    }

    private static string GenerateRecoveryCode()
    {
        const string alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"; // 去掉易混淆字符
        var bytes = RandomNumberGenerator.GetBytes(8);
        var sb = new StringBuilder();
        for (var i = 0; i < 8; i++) { if (i == 4) sb.Append('-'); sb.Append(alphabet[bytes[i] % alphabet.Length]); }
        return sb.ToString();
    }

    private static string GenerateToken()
    {
        var bytes = RandomNumberGenerator.GetBytes(32);
        return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }

    private static string Sha256(string value) =>
        Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(Encoding.ASCII.GetBytes(value))).ToLowerInvariant();
}
