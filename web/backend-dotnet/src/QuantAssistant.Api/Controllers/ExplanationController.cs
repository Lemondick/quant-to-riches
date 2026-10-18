using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1/analysis")]
public sealed class ExplanationController(ComputeService compute,AccountDatabase database):ControllerBase
{
    public sealed record ExplainBody(string DecisionRunId,string? UserText=null);
    [HttpPost("explain")]
    public async Task<IActionResult> Explain(ExplainBody body,CancellationToken cancellation)
    {
        if(body.DecisionRunId.Length is <1 or >128||body.UserText?.Length>2000)throw new ArgumentException("分析输入过长");
        var user=User.FindFirst("sub")!.Value;
        if(database.Read(sql=>sql.Scalar("SELECT 1 FROM strategy_runs WHERE user_id=$1 AND run_id=$2",user,body.DecisionRunId)) is null)
            throw new ApplicationFailure("decision_run_not_found",404,"未找到指定的决策运行。");
        return Ok(await compute.Run(user,"analysis.explain",body,cancellation,accountSnapshot:true));
    }
}
