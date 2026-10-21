namespace QuantAssistant.Core.Auth;

/// <summary>
/// 认证域错误码与状态码，与旧站点 services/auth.py 的 ERRORS 字典一一对应。
/// </summary>
public static class AuthErrors
{
    public const int SessionSeconds = 12 * 3600;
    public const int RecoverySeconds = 15 * 60;
    public const string CookieName = "quant_session";

    private static readonly IReadOnlyDictionary<string, (int Status, string Message)> Map =
        new Dictionary<string, (int, string)>
        {
            ["registration_closed"] = (409, "请先由管理者在本机初始化账号，随后开放注册。"),
            ["registration_full"] = (409, "注册人数已达到30人上限，暂不接受新账号。"),
            ["already_initialized"] = (409, "管理者账号已初始化，不能重复创建。"),
            ["username_taken"] = (409, "该用户名已注册，请更换用户名或登录。"),
            ["invalid_username"] = (422, "用户名须为3至32位字母、数字或下划线，以字母开头。"),
            ["invalid_password"] = (422, "密码长度须为15至128个字符，建议使用较长的密码短语。"),
            ["invalid_credentials"] = (401, "账号或密码错误。"),
            ["authentication_required"] = (401, "请登录后继续使用。"),
            ["admin_required"] = (403, "此功能仅限管理者使用。"),
            ["readonly_forbidden"] = (403, "只读账号不能执行写操作。"),
            ["csrf_invalid"] = (403, "请求校验失败，请刷新页面后重试。"),
            ["origin_forbidden"] = (403, "请求来源不被允许。"),
            ["rate_limited"] = (429, "请求过于频繁，请稍后重试。"),
            ["account_not_found"] = (404, "未找到该账号。"),
            ["user_data_unavailable"] = (503, "当前账号的数据暂不可用，请联系管理者检查。"),
            ["report_not_found"] = (404, "未找到该用户的报告。"),
            ["registration_paused"] = (409, "管理者已暂停注册，请稍后再试。"),
            ["admin_protected"] = (409, "管理者账号不能在后台停用或强制退出，请在账号设置管理自己的会话。"),
            ["session_not_found"] = (404, "未找到当前账号的有效会话。"),
            ["invalid_recovery_token"] = (400, "恢复凭证无效、已过期或已使用，请联系管理者重新申请。"),
            // batch2-11：双重验证。
            ["two_factor_required"] = (401, "账号已启用双重验证，请输入 6 位动态口令完成登录。"),
            ["invalid_totp_code"] = (401, "动态口令不正确或已过期，请重新输入。"),
        };

    public static int Status(string code) => Map.TryGetValue(code, out var v) ? v.Status : 500;

    public static string Message(string code) => Map.TryGetValue(code, out var v) ? v.Message : "服务器内部错误。";
}
