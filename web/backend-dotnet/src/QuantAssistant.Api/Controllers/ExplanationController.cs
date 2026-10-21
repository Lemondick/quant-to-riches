using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Research;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/analysis")]
public sealed class ExplanationController(ComputeService compute, DecisionRunRepository runs, ModelCallCacheService modelCache) : ControllerBase
{
    public sealed record ExplainBody(string DecisionRunId, string? UserText = null);

    [HttpPost("explain")]
    public async Task<IActionResult> Explain(ExplainBody body, CancellationToken cancellation)
    {
        if (body.DecisionRunId.Length is < 1 or > 128 || body.UserText?.Length > 2000) throw new ArgumentException("分析输入过长");
        var user = User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
        if (!await runs.ExistsAsync(user, body.DecisionRunId, cancellation))
            throw new ApplicationFailure("decision_run_not_found", 404, "未找到指定的决策运行。");
        // batch3-A：模型去重缓存（分析/报告链路的同步模型调用；worker 内部的报告模型调用见 ModelCallCacheService 注释）。
        const string modelVersion = "analysis.explain/v1";
        var prompt = new JsonObject { ["decision_run_id"] = body.DecisionRunId, ["user_text"] = body.UserText };
        var (hit, cached) = await modelCache.TryGetAsync(modelVersion, prompt, cancellation);
        if (hit && cached is JsonObject cachedObject)
        {
            cachedObject["cached"] = true;
            return Ok(cachedObject);
        }
        var result = await compute.Run(user, "analysis.explain", body, cancellation, accountSnapshot: true);
        if (result?.AsObject() is { } resultObject)
        {
            await modelCache.SetAsync(modelVersion, prompt, resultObject, cancellation);
            resultObject["cached"] = false;
        }
        return Ok(result);
    }
}
