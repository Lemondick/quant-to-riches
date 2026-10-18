using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// 管理员接口（P2 账号域）：用户列表、停用/恢复、强制退出、注册开关、审计、签发恢复码。
/// 所有管理操作同时记录审计；业务数据详情见 AdminDataController。
/// </summary>
[ApiController]
[Route("/api/v1/admin")]
public sealed class AdminController(AuthService auth, AdminDataRepository data) : ControllerBase
{
    public sealed record UserStatusBody(string Status);
    public sealed record RegistrationBody(bool Enabled);

    private Account? Admin => Request.Cookies.TryGetValue(AuthErrors.CookieName, out var token) ? auth.RequireAdmin(token) : null;

    private IActionResult NotAdmin() =>
        StatusCode(AuthErrors.Status("admin_required"), new ErrorResponse("admin_required", AuthErrors.Message("admin_required"), HttpContext.GetRequestId()));

    [HttpGet("users")]
    public IActionResult Users([FromQuery] string? search = null)
    {
        if (Admin is null)
        {
            return NotAdmin();
        }

        var result = JsonSerializer.SerializeToNode(auth.RegistrationStatus())!.AsObject();
        result["items"] = data.Users(search);
        return Ok(result);
    }

    [HttpPost("users/{userId}/status")]
    public IActionResult Status(string userId, [FromBody] UserStatusBody body)
    {
        var admin = Admin;
        if (admin is null)
        {
            return NotAdmin();
        }

        if (body.Status is not ("active" or "disabled"))
        {
            return UnprocessableEntity(new ErrorResponse("invalid_request", "status 须为 active 或 disabled。", HttpContext.GetRequestId()));
        }

        var target = body.Status == "active" ? AccountStatus.Active : AccountStatus.Disabled;
        var result = auth.SetUserStatus(admin.Id, userId, target);
        return result.Ok ? Ok(new { status = "updated" }) : MapError(result.ErrorCode!);
    }

    [HttpPost("users/{userId}/revoke-sessions")]
    public IActionResult RevokeSessions(string userId)
    {
        var admin = Admin;
        if (admin is null)
        {
            return NotAdmin();
        }

        auth.AdminRevokeSessions(admin.Id, userId);
        return Ok(new { status = "updated" });
    }

    [HttpPost("registration")]
    public IActionResult Registration([FromBody] RegistrationBody body)
    {
        var admin = Admin;
        if (admin is null)
        {
            return NotAdmin();
        }

        // 注册开关与 30 人硬上限独立（旧站点语义：enabled 不影响硬上限）。
        auth.SetRegistrationEnabled(admin.Id, body.Enabled);
        return Ok(auth.RegistrationStatus());
    }

    [HttpGet("audit")]
    public IActionResult Audit([FromQuery] int page = 1, [FromQuery] int limit = 50)
    {
        if (Admin is null)
        {
            return NotAdmin();
        }

        if(page is <1 or >100000 || limit is <1 or >100) throw new ArgumentException("分页参数越界");
        var (items, total) = auth.ListAudit(page, limit);
        return Ok(new
        {
            items = items.Select(i => new { actor_id = i.ActorId, action = i.Action, target_id = i.TargetId, detail = i.Detail, created_at = i.CreatedAt }),
            total,
        });
    }

    [HttpPost("users/{userId}/recovery-token")]
    public IActionResult RecoveryToken(string userId)
    {
        var admin = Admin;
        if (admin is null)
        {
            return NotAdmin();
        }

        var (token, expiresAt) = auth.IssueRecovery(userId, admin.Id);
        return StatusCode(StatusCodes.Status201Created, new { user_id = userId, token, expires_at = expiresAt });
    }

    private IActionResult MapError(string code) =>
        StatusCode(AuthErrors.Status(code), new ErrorResponse(code, AuthErrors.Message(code), HttpContext.GetRequestId()));
}
