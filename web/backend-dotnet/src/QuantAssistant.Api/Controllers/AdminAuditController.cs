using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务12：审计哈希链验证。
/// GET api/v1/admin/audit/verify：全链验证，返回 ok 或断裂位置（broken_at/expected/actual）。
/// 哈希链列由流 D 在 0036 添加；未添加时返回 audit_chain_not_enabled。
/// </summary>
[ApiController]
[Route("api/v1/admin/audit")]
public sealed class AdminAuditController(AuthService auth, AdminAuditRepository audits) : ControllerBase
{
    [HttpGet("verify")]
    public IActionResult Verify()
    {
        if (!Request.Cookies.TryGetValue(AuthErrors.CookieName, out var token) || auth.RequireAdmin(token) is null)
            return StatusCode(AuthErrors.Status("admin_required"),
                new ErrorResponse("admin_required", AuthErrors.Message("admin_required"), HttpContext.GetRequestId()));
        return Ok(audits.Verify());
    }
}
