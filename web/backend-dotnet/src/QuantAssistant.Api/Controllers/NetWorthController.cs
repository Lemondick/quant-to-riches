using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch5 任务2：净资产总览（api/v1/net-worth）。
/// GET /net-worth（总资产/负债/净资产 + 历史曲线 ?history_days=）；
/// GET /net-worth/allocation（股/债/现金/其他配置；账本资产类型仅 股票/基金/指数/现金，无债券档位时不计）。
/// </summary>
[ApiController]
[Route("api/v1/net-worth")]
public sealed class NetWorthController(NetWorthService service) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet]
    public async Task<IActionResult> Summary([FromQuery] int history_days = 90, CancellationToken cancellation = default) =>
        Ok(await service.SummaryAsync(Id, history_days, cancellation));

    [HttpGet("allocation")]
    public async Task<IActionResult> Allocation(CancellationToken cancellation = default) =>
        Ok(await service.AllocationAsync(Id, cancellation));
}
