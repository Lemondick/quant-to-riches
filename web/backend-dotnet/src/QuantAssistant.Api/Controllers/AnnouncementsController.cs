using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务8：基金公告。
/// GET api/v1/announcements 公告流查询（按持仓基金过滤）；POST api/v1/announcements/sync 手动触发抓取入库。
/// </summary>
[ApiController]
[Route("api/v1/announcements")]
public sealed class AnnouncementsController(AnnouncementRepository repo, AnnouncementService service, UserOperationAuditWriter audit) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet]
    public async Task<IActionResult> List([FromQuery] string? fund_code = null, [FromQuery] string? kind = null,
        [FromQuery] int page = 1, [FromQuery] int limit = 20, CancellationToken cancellation = default)
    {
        try
        {
            return Ok(await repo.ListAsync(Id, fund_code, kind, page, limit, cancellation));
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
    }

    [HttpPost("sync")]
    public async Task<IActionResult> Sync(CancellationToken cancellation = default)
    {
        var userId = Id;
        var result = await service.SyncForUserAsync(userId, cancellation);
        await audit.RecordAsync(userId, "announcements_synced", "fund_announcements", null, null,
            System.Text.Json.Nodes.JsonNode.Parse(result.ToJsonString()), cancellation);
        return Ok(result);
    }
}
