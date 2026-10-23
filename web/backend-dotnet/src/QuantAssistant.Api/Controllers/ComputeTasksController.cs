using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/compute/tasks")]
public sealed class ComputeTasksController(UserComputeTasks tasks) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet("{id}")]
    public async Task<IActionResult> Read(string id, CancellationToken cancellation)
    {
        Response.Headers.CacheControl = "no-store";
        return Ok(await tasks.ReadAsync(Id, id, cancellation));
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Cancel(string id, CancellationToken cancellation)
    {
        Response.Headers.CacheControl = "no-store";
        return Ok(await tasks.CancelAsync(Id, id, cancellation));
    }
}
