using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1")]
public sealed class LedgerReadController(AccountDatabase database) : ControllerBase
{
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static void Limit(int limit) { if (limit is < 1 or > 730) throw new ArgumentException("条数无效"); }

    [HttpGet("ledger/actions")]
    public async Task<IActionResult> Actions([FromQuery] int limit = 200, CancellationToken cancellation = default)
    {
        Limit(limit);
        return Ok(new { items = await database.QueryAsync("SELECT id,action_type,symbol,target_symbol,asset_type,quantity,target_quantity,price,target_price,gross_amount,fees,cash_delta,ratio,effective_at,external_id,source,from_account,to_account,note FROM corporate_action_events WHERE user_id=$1 ORDER BY effective_at DESC,id DESC LIMIT $2", [UserId, limit], cancellation) });
    }
    [HttpGet("ledger/corrections")]
    public async Task<IActionResult> Corrections([FromQuery] int limit = 200, CancellationToken cancellation = default)
    {
        Limit(limit);
        return Ok(new { items = await database.QueryAsync("SELECT id,target_type,target_id,reason,effective_at,external_id,source,note FROM ledger_corrections WHERE user_id=$1 ORDER BY effective_at DESC,id DESC LIMIT $2", [UserId, limit], cancellation) });
    }
    [HttpGet("portfolio/history")]
    public async Task<IActionResult> PortfolioHistory([FromQuery] int limit = 180, CancellationToken cancellation = default)
    {
        Limit(limit);
        return Ok(new { points = await database.QueryAsync("""
            SELECT run_id,as_of_date,generated_at,total_value,cash,unit_nav,high_water_mark,net_external_flow,is_baseline FROM
            (SELECT run_id,as_of_date,generated_at,total_value,cash,unit_nav,high_water_mark,net_external_flow,is_baseline FROM portfolio_valuations WHERE user_id=$1 ORDER BY as_of_date DESC,generated_at DESC,run_id DESC LIMIT $2) recent
            ORDER BY as_of_date,generated_at,run_id
            """, [UserId, limit], cancellation), currency = "CNY" });
    }
}
