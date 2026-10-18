using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1/ledger/replay")]
public sealed class LedgerReplayController(LedgerReplayMigrationService service):ControllerBase
{
    public sealed record ConfirmBody(string PreviewId,bool Confirmed);
    private string Id=>User.FindFirst("sub")!.Value;
    [HttpPost("preview")]public async Task<IActionResult> Preview(LedgerReplayMigrationService.HistoricalBody body,CancellationToken cancellation)=>Ok(await service.Preview(Id,body,cancellation));
    [HttpPost("batch-preview")]public async Task<IActionResult> BatchPreview(LedgerCsvInput.Batch body,CancellationToken cancellation)=>Ok(await service.BatchPreview(Id,body,cancellation));
    [HttpPost("confirm")]public async Task<IActionResult> Confirm(ConfirmBody body,CancellationToken cancellation)=>Ok(await service.Confirm(Id,body.PreviewId,body.Confirmed,cancellation));
    [HttpGet("history")]public IActionResult History(int page=1,int limit=20)=>Ok(service.History(Id,page,limit));
    [HttpGet("batch-history")]public IActionResult BatchHistory(int page=1,int limit=20)=>Ok(service.BatchHistory(Id,page,limit));
    [HttpGet("{previewId}/receipt")]public IActionResult Receipt(string previewId)=>Ok(service.Receipt(Id,previewId));
}
