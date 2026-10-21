using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Research;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/analysis")]
public sealed class ExplanationController(ComputeService compute, DecisionRunRepository runs) : ControllerBase
{
    public sealed record ExplainBody(string DecisionRunId, string? UserText = null);

    [HttpPost("explain")]
    public async Task<IActionResult> Explain(ExplainBody body, CancellationToken cancellation)
    {
        if (body.DecisionRunId.Length is < 1 or > 128 || body.UserText?.Length > 2000) throw new ArgumentException("分析输入过长");
        var user = User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
        if (!await runs.ExistsAsync(user, body.DecisionRunId, cancellation))
            throw new ApplicationFailure("decision_run_not_found", 404, "未找到指定的决策运行。");
        return Ok(await compute.Run(user, "analysis.explain", body, cancellation, accountSnapshot: true));
    }
}
