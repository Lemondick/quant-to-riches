using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务7：自然语言问答。
/// POST api/v1/ask：先匹配 10 个高频问题模板（确定性账本查询，不调模型、零成本）；
/// 未命中才走模型链路（受 F4 预算机制约束，超预算返回可操作提示）。
/// </summary>
[ApiController]
[Route("api/v1/ask")]
public sealed class AskController(AskService ask) : ControllerBase
{
    public sealed record AskBody(string Question);

    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpPost]
    public async Task<IActionResult> Ask([FromBody] AskBody body, CancellationToken cancellation = default)
    {
        if (string.IsNullOrWhiteSpace(body.Question) || body.Question.Length > 500)
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, "问题不能为空且不超过 500 字。", HttpContext.GetRequestId()));
        return Ok(await ask.AskAsync(Id, body.Question.Trim(), cancellation));
    }
}
