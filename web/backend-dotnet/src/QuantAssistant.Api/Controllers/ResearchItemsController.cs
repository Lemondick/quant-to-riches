using System.Text.Json.Nodes;
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
/// batch3 流 A：研报/资讯聚合。
/// GET api/v1/research：研报流查询（按标的/类型过滤分页）；
/// POST api/v1/research/refresh：调 worker op research.fetch 抓取入库（去重），记 user_operation_audit。
/// </summary>
[ApiController]
[Route("api/v1/research")]
public sealed class ResearchItemsController(ResearchItemService service, ResearchItemRepository repo, UserOperationAuditWriter audit) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    public sealed class RefreshBody
    {
        public string[]? Symbols { get; set; }
        public string[]? Kinds { get; set; }
    }

    [HttpGet]
    public async Task<IActionResult> List([FromQuery] string? symbol = null, [FromQuery] string? kind = null,
        [FromQuery] int page = 1, [FromQuery] int limit = 20, CancellationToken cancellation = default)
    {
        try
        {
            return Ok(await repo.ListAsync(symbol, kind, page, limit, cancellation));
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
    }

    [HttpPost("refresh")]
    public async Task<IActionResult> Refresh([FromBody] RefreshBody? body, CancellationToken cancellation = default)
    {
        var userId = Id;
        if (body?.Symbols is { Length: > 50 } || body?.Symbols?.Any(s => s is not { Length: > 0 and <= 32 }) == true)
            throw new ArgumentException("symbols 无效（最多 50 个，每项 1–32 字符）。");
        if (body?.Kinds is { Length: > 2 } || body?.Kinds?.Any(k => k is not ("report" or "news")) == true)
            throw new ArgumentException("kinds 须为 report/news 子集。");
        var result = await service.RefreshForUserAsync(userId, body?.Symbols, body?.Kinds, cancellation);
        await audit.RecordAsync(userId, "research_refreshed", "research_items", null, null,
            new JsonObject
            {
                ["fetched"] = result["fetched"]?.DeepClone(), ["inserted"] = result["inserted"]?.DeepClone(),
                ["deduped"] = true,
            }, cancellation);
        return Ok(result);
    }
}
