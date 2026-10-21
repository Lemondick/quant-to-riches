using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务11：双重验证（TOTP RFC6238 自研 + 恢复码）。
/// 登录流程：密码通过→若启用2FA→POST login 返回 two_factor_required + pending_token→
/// POST api/v1/auth/2fa/verify{pending_token,code}→通过才发 session。
/// 启用/关闭/确认/恢复码 regenerating 四个端点均需登录态；写操作记 user_operation_audit。
/// </summary>
[ApiController]
[Route("api/v1/auth/2fa")]
public sealed class TwoFactorController(AuthService auth, QuantOptions options, UserOperationAuditWriter audit, IWebHostEnvironment environment) : ControllerBase
{
    public sealed record CodeBody(string Code);
    public sealed record VerifyBody(string PendingToken, string Code);
    public sealed record DisableBody(string Password);

    private Account Current =>
        HttpContext.Items[typeof(AuthSession)] as AuthSession is { Account: var account } ? account
            : throw new AuthFailure("authentication_required");

    private IActionResult MapError(string code) =>
        StatusCode(AuthErrors.Status(code), new ErrorResponse(code, AuthErrors.Message(code), HttpContext.GetRequestId()));

    [HttpGet("status")]
    public IActionResult Status()
    {
        var (_, enabled) = auth.TotpState(Current.Id);
        return Ok(new { enabled });
    }

    /// <summary>启用第一步：生成密钥（未启用），返回密钥与扫码 URI（用户用验证器 App 扫码）。</summary>
    [HttpPost("enable")]
    public IActionResult Enable()
    {
        var user = Current;
        if (auth.TotpState(user.Id).Enabled)
            return Conflict(new ErrorResponse(ErrorCodes.InvalidRequest, "双重验证已启用。", HttpContext.GetRequestId()));
        var (secret, uri) = auth.BeginTotpEnrollment(user.Id, options.TotpIssuer);
        return Ok(new { secret, provisioning_uri = uri, issuer = options.TotpIssuer });
    }

    /// <summary>启用第二步：校验动态口令，通过则正式启用并签发 10 个恢复码（明文仅返回一次）。</summary>
    [HttpPost("confirm")]
    public IActionResult Confirm([FromBody] CodeBody body)
    {
        var user = Current;
        try
        {
            var codes = auth.ConfirmTotpEnrollment(user.Id, body.Code);
            audit.Record(user.Id, "totp_enabled", "user_totp", user.Id, null, JsonNode.Parse("{\"enabled\":true}"));
            return Ok(new { enabled = true, recovery_codes = codes });
        }
        catch (AuthFailure error)
        {
            return MapError(error.Code);
        }
    }

    /// <summary>关闭：需验证密码；清除密钥与恢复码。</summary>
    [HttpPost("disable")]
    public IActionResult Disable([FromBody] DisableBody body)
    {
        var user = Current;
        try
        {
            auth.DisableTotp(user.Id, body.Password);
        }
        catch (AuthFailure error)
        {
            return MapError(error.Code);
        }
        audit.Record(user.Id, "totp_disabled", "user_totp", user.Id, JsonNode.Parse("{\"enabled\":true}"), JsonNode.Parse("{\"enabled\":false}"));
        return Ok(new { enabled = false });
    }

    /// <summary>重新生成恢复码：作废旧码，明文仅返回一次。</summary>
    [HttpPost("recovery-codes/regenerate")]
    public IActionResult RegenerateRecoveryCodes()
    {
        var user = Current;
        var codes = auth.RegenerateRecoveryCodes(user.Id);
        audit.Record(user.Id, "totp_recovery_regenerated", "user_totp", user.Id, null, null);
        return Ok(new { recovery_codes = codes });
    }

    /// <summary>登录第二步（公开）：pending_token + 6 位码（或恢复码）→ 通过才发 session。</summary>
    [HttpPost("verify")]
    public IActionResult Verify([FromBody] VerifyBody body)
    {
        var account = auth.VerifyTotpChallenge(body.PendingToken, body.Code);
        if (account is null) return MapError("invalid_totp_code");
        var session = auth.CreateSession(account.Id, "未知设备");
        Response.Cookies.Append(AuthErrors.CookieName, session.Token, new CookieOptions
        {
            HttpOnly = true,
            SameSite = SameSiteMode.Lax,
            Secure = Request.IsHttps || !environment.IsDevelopment(),
            Path = "/",
            Expires = session.ExpiresAt,
        });
        return Ok(new
        {
            user = new { id = account.Id, username = account.Username,
                role = account.Role switch { AccountRole.Admin => "admin", AccountRole.ReadOnly => "readonly", _ => "user" } },
            csrf_token = session.Csrf, expires_at = session.ExpiresAt,
        });
    }
}
