using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Ledger;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/ledger")]
public sealed class LedgerReadController(LedgerReadRepository ledger) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static void Limit(int limit)
    { if (limit is < 1 or > 500) throw new ArgumentException("条数无效"); }

    [HttpGet("corporate-actions")]
    public async Task<IActionResult> CorporateActions([FromQuery] int limit = 100, CancellationToken cancellation = default)
    { Limit(limit); return Ok(new { items = await ledger.CorporateActionsAsync(Id, limit, cancellation) }); }

    [HttpGet("corrections")]
    public async Task<IActionResult> Corrections([FromQuery] int limit = 100, CancellationToken cancellation = default)
    { Limit(limit); return Ok(new { items = await ledger.CorrectionsAsync(Id, limit, cancellation) }); }

    [HttpGet("portfolio-history")]
    public async Task<IActionResult> PortfolioHistory([FromQuery] int limit = 100, CancellationToken cancellation = default)
    { Limit(limit); return Ok(new { items = await ledger.PortfolioHistoryAsync(Id, limit, cancellation) }); }
}
