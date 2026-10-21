using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/runtime")]
public sealed class RuntimeController(RuntimeViewService runtime) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet("status")]
    public async Task<IActionResult> Status(CancellationToken cancellation = default) =>
        Ok(await runtime.StatusAsync(User, cancellation));

    [HttpGet("workers/snapshot")]
    public async Task<IActionResult> WorkerSnapshot(CancellationToken cancellation = default) =>
        Ok(await runtime.WorkerSnapshotAsync(Id, cancellation));
}
