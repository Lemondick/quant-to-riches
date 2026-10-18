using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/funds/{symbol}")]
public sealed class FundFeeController(FundFeeRepository fees) : ControllerBase
{
    public sealed record EvidenceBody(string ShareClass, decimal PurchaseRate, decimal AnnualServiceRate, RedemptionTier[] RedemptionTiers,
        DateOnly VerifiedOn, DateOnly EffectiveFrom, string EvidenceReference, PurchaseTier[]? PurchaseTiers = null,
        DateOnly? EffectiveTo = null, string Channel = "alipay", ServiceRefundRule? ServiceRefundRule = null, bool Confirmed = false);
    public sealed record QuoteBody(decimal Amount, int HoldingDays, DateOnly? ConfirmedOn = null, bool NavIncludesServiceFee = false);
    public sealed record LotBody(DateOnly ConfirmedOn, decimal GrossAmount);
    public sealed record RedemptionBody(DateOnly ConfirmedOn, LotBody[] Lots);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    [HttpGet("fee-evidence")]
    public IActionResult Read(string symbol) => Ok(fees.List(UserId, symbol));
    [HttpPost("fee-evidence")]
    public IActionResult Save(string symbol, [FromBody] EvidenceBody body)
    {
        if(!body.Confirmed)throw new ArgumentException("请明确确认已人工核对费率依据");
        return Ok(fees.Save(UserId, new(symbol, body.ShareClass, body.PurchaseRate, body.AnnualServiceRate, body.RedemptionTiers,
            body.VerifiedOn, body.EffectiveFrom, body.EffectiveTo, body.PurchaseTiers, body.Channel, body.EvidenceReference, body.ServiceRefundRule)));
    }
    [HttpPost("fee-quote")]
    public IActionResult Quote(string symbol, [FromBody] QuoteBody body) => Ok(fees.Quote(UserId, symbol, body.Amount, body.HoldingDays, body.ConfirmedOn, body.NavIncludesServiceFee));
    [HttpPost("redemption-quote")]
    public IActionResult Redemption(string symbol, [FromBody] RedemptionBody body)
    {if(body.Lots.Length is <1 or >100)throw new ArgumentException("赎回批次须为1至100条");return Ok(fees.RedemptionQuote(UserId, symbol, body.Lots.Select(l => (l.ConfirmedOn, l.GrossAmount)).ToArray(), body.ConfirmedOn));}
}
