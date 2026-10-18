using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;
[ApiController]
[Route("api/v1/admin/calendar")]
public sealed class CalendarController(TradingCalendarRepository calendars) : ControllerBase
{
    public sealed record SaveBody(int Year, string Source, string[] Holidays, string? ExpectedRevision = null);
    [HttpGet]
    public IActionResult Read([FromQuery] int? year = null) => Ok(calendars.View(year ?? DateTime.UtcNow.AddHours(8).Year));
    [HttpGet("status")]
    public IActionResult Status() => Ok(calendars.Status(DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8))));
    [HttpPut]
    public IActionResult Save([FromBody] SaveBody body) => Ok(calendars.Save(User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required"), new(body.Year, body.Source, body.Holidays), body.ExpectedRevision));
}
