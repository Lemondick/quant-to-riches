using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1")]
public sealed class PhotoController(PhotoMigrationService service):ControllerBase
{
    public sealed record ManualBody(DateOnly AsOfDate);
    public sealed record MergeBody(string[] PreviewIds);
    public sealed record PreviewBody(string ImageBase64,DateOnly? AsOfDate=null,bool ExternalOcrConsent=false,bool HoldingsOnly=false);
    public sealed record ReviewBody(string PreviewId,PhotoMigrationService.PhotoRow[] Positions,string Cash,bool Confirmed=false,string Mode="initial",string ExternalFlow="0");
    public sealed record HoldingsBody(string PreviewId,PhotoMigrationService.PhotoRow[] Positions);
    public sealed record CommitBody(string ReviewId,bool Confirmed=false);
    private string Id=>User.FindFirst("sub")!.Value;
    [HttpGet("portfolio/photo-holdings")]public IActionResult Holdings()=>Ok(service.Holdings(Id));
    [HttpPost("imports/fund-photo/manual")]public IActionResult Manual(ManualBody body)=>Ok(service.Manual(Id,body.AsOfDate));
    [HttpPost("imports/fund-photo/merge")]public IActionResult Merge(MergeBody body)=>Ok(service.Merge(Id,body.PreviewIds));
    [HttpPost("imports/fund-photo/preview")]
    public async Task<IActionResult> Preview(PreviewBody body,CancellationToken cancellation)=>Ok(await service.Recognize(Id,body.ImageBase64,body.AsOfDate,body.ExternalOcrConsent,body.HoldingsOnly,cancellation));
    [HttpPost("imports/fund-photo/review")]
    public async Task<IActionResult> Review(ReviewBody body,CancellationToken cancellation)=>Ok(await service.Review(Id,body.PreviewId,body.Positions,body.Cash,body.Mode,body.ExternalFlow,false,cancellation));
    [HttpPost("imports/fund-photo/holdings-review")]
    public async Task<IActionResult> HoldingsReview(HoldingsBody body,CancellationToken cancellation)=>Ok(await service.Review(Id,body.PreviewId,body.Positions,null,"initial","0",true,cancellation));
    [HttpPost("imports/fund-photo/commit")]public IActionResult Commit(CommitBody body)=>Ok(service.Commit(Id,body.ReviewId,body.Confirmed,false));
    [HttpPost("imports/fund-photo/holdings-commit")]public IActionResult HoldingsCommit(CommitBody body)=>Ok(service.Commit(Id,body.ReviewId,body.Confirmed,true));
    [HttpPost("imports/fund-photo/confirm")]
    public async Task<IActionResult> Confirm(ReviewBody body,CancellationToken cancellation)
    {
        if(!body.Confirmed)throw new ArgumentException("请先核对完整基金持仓和现金并确认");
        return Ok(await service.Confirm(Id,body.PreviewId,body.Positions,body.Cash,cancellation));
    }
}
