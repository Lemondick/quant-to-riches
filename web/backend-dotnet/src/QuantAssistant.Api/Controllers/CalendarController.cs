using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;
[ApiController]
[Route("api/v1/admin/calendar")]
public sealed class CalendarController(TradingCalendarRepository calendars) : ControllerBase
{
    public sealed record SaveBody(int Year, string Source, string[] Holidays, string? ExpectedRevision = null, string Market = "CN");
    [HttpGet]
    public IActionResult Read([FromQuery] int? year = null, [FromQuery] string market = "CN") => Ok(calendars.View(year ?? DateTime.UtcNow.AddHours(8).Year, market));
    [HttpGet("status")]
    public IActionResult Status([FromQuery] string market = "CN") => Ok(calendars.Status(DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)), market));
    [HttpPut]
    public IActionResult Save([FromBody] SaveBody body) => Ok(calendars.Save(User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required"), new(body.Year, body.Source, body.Holidays, body.Market), body.ExpectedRevision));
}
