using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/runtime/costs")]
public sealed class CostController(ModelCostRepository costs) : ControllerBase
{
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    [HttpGet]
    public IActionResult Read() => Ok(costs.Read(UserId));
    [HttpPut]
    public IActionResult Save([FromBody] CostSettings body) => Ok(costs.Save(UserId, body));
    [HttpPost("{identity}/bill-evidence")]
    public IActionResult RecordBill(string identity,[FromBody] ModelBillEvidence body)=>Ok(costs.RecordBill(UserId,identity,body));
}
