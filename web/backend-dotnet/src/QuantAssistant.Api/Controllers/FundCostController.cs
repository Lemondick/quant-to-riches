using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch3 流 A：持有成本。
/// GET api/v1/cost/positions：每只基金持仓成本/浮动盈亏/年化收益率/回本线（费率口径复用 FundFeeRules）。
/// </summary>
[ApiController]
[Route("api/v1/cost")]
public sealed class FundCostController(FundCostService costs) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet("positions")]
    public IActionResult Positions()
    {
        try
        {
            return Ok(costs.Positions(Id));
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
    }
}
