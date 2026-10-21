using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务4：基金体检报告读取（报告由 Scheduler 按 QUANT_HEALTH_CHECK_CRON 生成，存 site_settings）。
/// </summary>
[ApiController]
[Route("api/v1/funds/health")]
public sealed class FundHealthController(AccountDatabase database, FundMaintenanceService maintenance) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet("latest")]
    public IActionResult Latest()
    {
        var raw = database.Read(sql => sql.Scalar("SELECT value FROM site_settings WHERE key=$1", "fund_health_check_latest:" + Id)) as string;
        return Ok(new
        {
            report = raw is null ? null : JsonNode.Parse(raw),
            cron = "QUANT_HEALTH_CHECK_CRON",
        });
    }

    [HttpPost("run")]
    public IActionResult RunNow()
    {
        // 手动触发一次体检（复用 Scheduler 逻辑，仍走 worker op fund.health_check）。
        maintenance.CheckFundHealthForced(Id);
        return Ok(new { status = "triggered" });
    }
}
