using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/funds/{symbol}")]
public sealed class FundLotController(FundLotRepository lots) : ControllerBase
{
    public sealed record LotsBody(FundLot[] Items, DateOnly VerifiedOn, string EvidenceReference, string PositionFingerprint, bool Confirmed = false);
    public sealed record QuoteBody(long SnapshotId, FundLotSelection[] Selections, DateOnly ConfirmedOn, decimal AssumedNav);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    [HttpGet("holding-lots")]
    public IActionResult Read(string symbol) => Ok(lots.List(UserId, symbol));
    [HttpPost("holding-lots")]
    public IActionResult Save(string symbol, [FromBody] LotsBody body) => Ok(lots.Save(UserId, symbol, body.Items, body.VerifiedOn, body.EvidenceReference, body.PositionFingerprint, body.Confirmed));
    [HttpPost("holding-redemption-quote")]
    public IActionResult Quote(string symbol, [FromBody] QuoteBody body) => Ok(lots.Quote(UserId, symbol, body.SnapshotId, body.Selections, body.ConfirmedOn, body.AssumedNav));
}
