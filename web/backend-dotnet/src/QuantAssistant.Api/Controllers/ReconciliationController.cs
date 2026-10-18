using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1/ledger/reconciliation")]
public sealed class ReconciliationController(ReconciliationRepository repository):ControllerBase
{
    public sealed record ResolveBody(string Resolution,string Note);
    private string Id=>User.FindFirst("sub")!.Value;
    [HttpGet("accounts")]public IActionResult Accounts()=>Ok(repository.Accounts(Id));
    [HttpPost("compare")]public IActionResult Compare(CompareAccounts body)=>Ok(repository.Compare(Id,body));
    [HttpGet("history")]public IActionResult History(int page=1,int limit=20)=>Ok(repository.History(Id,page,limit));
    [HttpGet("{identity}")]public IActionResult Detail(string identity)=>Ok(repository.Detail(Id,identity));
    [HttpPost("{identity}/resolve")]public IActionResult Resolve(string identity,ResolveBody body)=>Ok(repository.Resolve(Id,identity,body.Resolution,body.Note));
}
