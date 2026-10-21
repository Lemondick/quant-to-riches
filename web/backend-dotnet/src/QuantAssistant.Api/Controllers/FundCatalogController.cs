using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1/funds")]
public sealed class FundCatalogController(FundCatalogService catalog):ControllerBase
{
    private string Id=>User.FindFirst("sub")!.Value;
    [HttpGet("categories")]
    public async Task<IActionResult> Categories(CancellationToken cancellation)=>Ok(await catalog.ReadAsync(Id,new("",1,50,new()),true,cancellation));
    [HttpGet("catalog")]
    public async Task<IActionResult> Catalog(CancellationToken cancellation,string q="",int page=1,[FromQuery(Name="page_size")]int pageSize=50,
        [FromQuery(Name="fund_type")]string[]? type=null,[FromQuery(Name="asset_class")]string[]? asset=null,
        [FromQuery(Name="investment_scope")]string[]? scope=null,[FromQuery(Name="strategy_tags")]string[]? tags=null,
        [FromQuery(Name="product_structure")]string[]? structure=null,[FromQuery(Name="benchmark_index_id")]string[]? benchmark=null,
        [FromQuery(Name="share_class")]string[]? share=null,[FromQuery(Name="regulatory_category")]string[]? regulatory=null,
        [FromQuery(Name="classification_status")]string[]? status=null)
        =>Ok(await catalog.ReadAsync(Id,new(q,page,pageSize,new(){["fund_type"]=type??[],["asset_class"]=asset??[],["investment_scope"]=scope??[],
            ["strategy_tags"]=tags??[],["product_structure"]=structure??[],["benchmark_index_id"]=benchmark??[],["share_class"]=share??[],
            ["regulatory_category"]=regulatory??[],["classification_status"]=status??[]}),false,cancellation));
}
