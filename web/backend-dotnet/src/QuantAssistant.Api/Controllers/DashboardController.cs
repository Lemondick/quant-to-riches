using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/dashboard")]
public sealed class DashboardController(DashboardSummaryService summary, TradingCalendarRepository calendars) : ControllerBase
{
    [HttpGet("summary")]
    public async Task<IActionResult> Read(CancellationToken cancellation,bool separateNav=false)
    {
        Response.Headers.CacheControl = "no-store";
        var result=await summary.Read(User.FindFirst("sub")!.Value, cancellation, (HttpContext.Items[typeof(AuthSession)] as AuthSession)?.DataVersion);
        if(!separateNav && result["valuations"] is System.Text.Json.Nodes.JsonObject valuations)PublicDataQuality.Apply("fund.valuations",valuations,DateTimeOffset.UtcNow,false,calendars.CurrentSnapshot);
        return Ok(result);
    }
}
