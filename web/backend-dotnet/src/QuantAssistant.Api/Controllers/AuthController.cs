using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// 认证控制器（P2 核心流程：注册、登录、登出、会话、恢复码）。
/// 契约与旧站点 /api/v1/auth/* 一致：会话 Cookie 名 quant_session（12 小时），恢复码 15 分钟。
/// </summary>
[ApiController]
[Route("/api/v1/auth")]
public sealed class AuthController(AuthService auth, IWebHostEnvironment environment) : ControllerBase
{
    public sealed record RegisterBody(string Username, string Password);
    public sealed record LoginBody(string Username, string Password);
    public sealed record RedeemBody(string Username, string Token, string NewPassword);
    public sealed record PasswordBody(string CurrentPassword, string NewPassword);

    [HttpGet("sessions")]
    public IActionResult Sessions() => Ok(auth.Sessions(Request.Cookies[AuthErrors.CookieName]!));

    [HttpPost("sessions/revoke-others")]
    public IActionResult RevokeOtherSessions() => Ok(auth.RevokeSessions(Request.Cookies[AuthErrors.CookieName]!));

    [HttpPost("sessions/{session_id}/revoke")]
    public IActionResult RevokeSession(string session_id)
    {
        var token = Request.Cookies[AuthErrors.CookieName]!;
        var result = auth.RevokeSessions(token, session_id);
        if (auth.SessionAccount(token) is null) Response.Cookies.Delete(AuthErrors.CookieName);
        return Ok(result);
    }

    [HttpPost("password")]
    public IActionResult Password([FromBody] PasswordBody body)
    {
        auth.ChangePassword(Request.Cookies[AuthErrors.CookieName]!, body.CurrentPassword, body.NewPassword);
        Response.Cookies.Delete(AuthErrors.CookieName);
        return Ok(new { status = "password_changed" });
    }

    [HttpGet("status")]
    public IActionResult RegistrationStatus() => Ok(auth.RegistrationStatus());

    [HttpPost("register")]
    public IActionResult Register([FromBody] RegisterBody body)
    {
        var result = auth.Register(body.Username, body.Password);
        return result.Ok
            ? StatusCode(StatusCodes.Status201Created, new { user = PublicUser(result.Account!) })
            : MapError(result.ErrorCode!);
    }

    [HttpPost("login")]
    public IActionResult Login([FromBody] LoginBody body)
    {
        var account = auth.Authenticate(body.Username, body.Password);
        if (account is null)
        {
            return MapError("invalid_credentials");
        }

        var session = auth.CreateSession(account.Id, "未知设备");
        Response.Cookies.Append(AuthErrors.CookieName, session.Token, new CookieOptions
        {
            HttpOnly = true,
            SameSite = SameSiteMode.Lax,
            Secure = Request.IsHttps || !environment.IsDevelopment(),
            Path = "/",
            Expires = session.ExpiresAt,
        });
        return Ok(new { user = PublicUser(auth.SessionAccount(session.Token)!), csrf_token = session.Csrf, expires_at = session.ExpiresAt });
    }

    [HttpPost("logout")]
    public IActionResult Logout()
    {
        if (Request.Cookies.TryGetValue(AuthErrors.CookieName, out var token))
        {
            auth.RevokeSession(token);
        }

        Response.Cookies.Delete(AuthErrors.CookieName);
        return Ok(new { status = "logged_out" });
    }

    [HttpGet("session")]
    public IActionResult Session()
    {
        if (!Request.Cookies.TryGetValue(AuthErrors.CookieName, out var token))
        {
            return MapError("authentication_required");
        }

        var session = HttpContext.Items[typeof(AuthSession)] as AuthSession;
        if (session is null)
        {
            return MapError("authentication_required");
        }

        Response.Headers.CacheControl = "no-store";
        return Ok(new { user = PublicUser(session.Account), csrf_token = session.Csrf, expires_at = session.ExpiresAt, cache_scope = session.SessionId });
    }

    [HttpPost("recovery/redeem")]
    public IActionResult Redeem([FromBody] RedeemBody body)
    {
        var result = auth.RedeemRecovery(body.Username, body.Token, body.NewPassword);
        if (!result.Ok) return MapError(result.ErrorCode!);
        Response.Cookies.Delete(AuthErrors.CookieName, new CookieOptions
        {
            Path = "/", HttpOnly = true, SameSite = SameSiteMode.Lax,
            Secure = Request.IsHttps || !environment.IsDevelopment(),
        });
        return Ok(new { status = "password_recovered" });
    }

    private IActionResult MapError(string code) =>
        StatusCode(AuthErrors.Status(code), new ErrorResponse(code, AuthErrors.Message(code), HttpContext.GetRequestId()));

    private static object PublicUser(Account account) =>
        new { id = account.Id, username = account.Username, role = account.Role switch { AccountRole.Admin => "admin", AccountRole.ReadOnly => "readonly", _ => "user" }, status = account.Status == AccountStatus.Active ? "active" : "disabled",
            created_at=account.CreatedAt,last_login_at=account.LastLoginAt };
}
