using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/admin/users/{userId}")]
public sealed class AdminDataController(AdminDataRepository data) : ControllerBase
{
    private string Actor => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    [HttpGet("details")]
    public IActionResult Details(string userId, [FromQuery] string section = "holdings", [FromQuery] int page = 1, [FromQuery] int limit = 50, [FromQuery] string symbol = "", [FromQuery] DateOnly? start = null, [FromQuery] DateOnly? end = null)
        => Ok(data.Details(Actor, userId, section, page, limit, symbol, start, end));
    [HttpGet("export")]
    public IActionResult Export(string userId, [FromQuery] string section = "holdings", [FromQuery] string symbol = "", [FromQuery] DateOnly? start = null, [FromQuery] DateOnly? end = null)
        => Ok(data.Details(Actor, userId, section, 1, 100, symbol, start, end, export: true));
    [HttpGet("reports/{source}/{reportId}")]
    public IActionResult Report(string userId, string source, string reportId, [FromQuery(Name = "attempt_no")] int attemptNo = 1) => Ok(data.Report(Actor, userId, source, reportId, attemptNo));
    [HttpGet("usage")]
    public IActionResult Usage(string userId) => Ok(data.Usage(Actor, userId));
    [HttpGet("timeline")]
    public IActionResult Timeline(string userId, [FromQuery] string category = "all", [FromQuery] int page = 1, [FromQuery] int limit = 50, [FromQuery] DateOnly? start = null, [FromQuery] DateOnly? end = null)
        => Ok(data.Timeline(Actor, userId, category, page, limit, start, end));
}
