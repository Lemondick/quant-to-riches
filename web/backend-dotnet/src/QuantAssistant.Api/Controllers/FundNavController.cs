using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1/portfolio/fund-nav")]
public sealed class FundNavController(FundNavMigrationService service):ControllerBase
{
    public sealed record Body(DateOnly AsOfDate,bool Confirmed=false,string? PreviewId=null);
    [HttpPost]public async Task<IActionResult> Run(Body body,CancellationToken cancellation)=>Ok(await service.Run(User.FindFirst("sub")!.Value,body.AsOfDate,body.Confirmed,body.PreviewId,cancellation));
}
