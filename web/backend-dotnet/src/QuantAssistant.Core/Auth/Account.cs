namespace QuantAssistant.Core.Auth;

public static class AccountRules
{
    public const int AccountLimit = 30;

    // 与旧站点 services/auth.py 一致：用户名 3-32 字符小写字母开头；密码 15-128 字符。
    public static bool IsValidUsername(string? value)
    {
        if (value is null)
        {
            return false;
        }

        var normalized = value.Trim().ToLowerInvariant();
        if (normalized.Length is < 3 or > 32)
        {
            return false;
        }

        if (normalized[0] is < 'a' or > 'z')
        {
            return false;
        }

        return normalized.All(c => c is (>= 'a' and <= 'z') or (>= '0' and <= '9') or '_');
    }

    public static bool IsValidPassword(string? value) =>
        value is not null && value.Length is >= 15 and <= 128;
}

public enum AccountRole
{
    User,
    Admin,
}

public enum AccountStatus
{
    Active,
    Disabled,
}

public sealed record Account(string Id, string Username, string PasswordHash, AccountRole Role, AccountStatus Status,
    DateTime? CreatedAt=null,DateTime? LastLoginAt=null);
public sealed record AuthSession(Account Account, string Csrf, DateTime ExpiresAt, string? SessionId = null, string? DataVersion = null);

/// <summary>用户名冲突（映射 409），对应旧站点 AccountConflict。</summary>
public sealed class AccountConflict(string username) : Exception($"username already exists: {username}");

/// <summary>账号不存在（映射 404 account_not_found）。</summary>
public sealed class AccountNotFound(string userId) : Exception($"account not found: {userId}");

public sealed class AuthFailure(string code) : Exception(code)
{
    public string Code { get; } = code;
}
