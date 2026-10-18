using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Controllers;
[ApiController]
[Route("api/v1/advisor")]
public sealed class ModelConnectionController(ModelConnectionService connections) : ControllerBase
{
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    [HttpGet("model-settings")]
    public IActionResult Read() => Ok(connections.Read(UserId));
    [HttpPut("model-settings")]
    public IActionResult Save([FromBody] ModelConnectionService.SaveModelsBody body) => Ok(connections.SaveModels(UserId, body));
    [HttpPost("models/discover")]
    public async Task<IActionResult> Discover([FromBody] ModelConnectionService.ProviderBody body, CancellationToken cancellation) => Ok(await connections.Discover(UserId, body, cancellation));
    [HttpPut("jev-connection")]
    public IActionResult Jev([FromBody] ModelConnectionService.JevBody body) => Ok(connections.SaveJev(UserId, body));
    [HttpPost("jev-models/discover")]
    public async Task<IActionResult> DiscoverJev([FromBody] ModelConnectionService.JevBody body, CancellationToken cancellation) => Ok(await connections.DiscoverJev(UserId, body, cancellation));
}
