using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Runtime;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// 管理员接口（P2 账号域）：用户列表、停用/恢复、强制退出、注册开关、审计、签发恢复码。
/// 所有管理操作同时记录审计；业务数据详情见 AdminDataController。
/// </summary>
[ApiController]
[Route("/api/v1/admin")]
public sealed class AdminController(AuthService auth, AdminDataRepository data, AdvisorExecutionService advisor, UserOperationAuditWriter audit) : ControllerBase
{
    public sealed record UserStatusBody(string Status);
    public sealed record RegistrationBody(bool Enabled);
    public sealed record UserRoleBody(string Role);
    public sealed record ScheduleRerunBody(string UserId, DateOnly RunDate, string Kind, bool Confirm = false);

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

    /// <summary>F6：在 user ↔ readonly 之间调整用户角色（管理者身份不受此路径变更）。</summary>
    [HttpPost("users/{userId}/role")]
    public IActionResult Role(string userId, [FromBody] UserRoleBody body)
    {
        var admin = Admin;
        if (admin is null)
        {
            return NotAdmin();
        }

        var role = body.Role switch { "user" => AccountRole.User, "readonly" => AccountRole.ReadOnly, _ => (AccountRole?)null };
        if (role is null)
        {
            return UnprocessableEntity(new ErrorResponse("invalid_request", "role 须为 user 或 readonly。", HttpContext.GetRequestId()));
        }

        var result = auth.SetUserRole(admin.Id, userId, role.Value);
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

    /// <summary>F9：管理者手动重跑定时报告。窗口外需 confirm=true 二次确认。</summary>
    [HttpPost("advisor/schedule/rerun")]
    public IActionResult RerunSchedule([FromBody] ScheduleRerunBody body)
    {
        var admin = Admin;
        if (admin is null)
        {
            return NotAdmin();
        }

        if (string.IsNullOrWhiteSpace(body.UserId)) throw new ArgumentException("用户无效");
        var report = advisor.RerunSchedule(body.UserId, body.RunDate, body.Kind, body.Confirm);
        audit.Record(admin.Id, "advisor.schedule.rerun", "advisor_schedule",
            $"{body.UserId}/{body.RunDate:yyyy-MM-dd}/{body.Kind}", null,
            new JsonObject { ["report_id"] = report["report_id"]?.DeepClone(), ["status"] = report["status"]?.DeepClone() });
        return Ok(report);
    }

    private IActionResult MapError(string code) =>
        StatusCode(AuthErrors.Status(code), new ErrorResponse(code, AuthErrors.Message(code), HttpContext.GetRequestId()));
}
