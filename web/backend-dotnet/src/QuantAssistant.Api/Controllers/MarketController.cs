using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Jobs;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1")]
public sealed class MarketController(ComputeService compute,ILedgerRepository ledger,PublicMarketSnapshot snapshots,PublicSnapshotRepository publicData):ControllerBase
{
    private string Id=>User.FindFirst("sub")!.Value;
    private async Task<IActionResult> ReadPublic(string kind,object input,CancellationToken cancellation,bool refresh)
    { var result=await compute.ReadPublic(Id,kind,input,cancellation,refresh);if(result.Data is System.Text.Json.Nodes.JsonObject data)PublicDataQuality.Apply(kind,data,DateTimeOffset.UtcNow);Response.Headers["X-Public-Cache"]=result.Cache;return Ok(result.Data); }
    [HttpGet("market/snapshot")]
    public Task<IActionResult> Snapshot(string? section,CancellationToken cancellation,bool refresh=false,bool fullCatalog=false)
    {var result=snapshots.Read(section,refresh);if(!fullCatalog && result.Data is System.Text.Json.Nodes.JsonObject data)PublicMarketSnapshot.UseLegacySectors(data);Response.Headers["X-Public-Cache"]=result.Cache;return Task.FromResult<IActionResult>(Ok(result.Data));}
    private static string[] Codes(string[]? values)
    {if(values?.Length>50)throw new ArgumentException("行情标的过多");return (values??[]).Select(value=>{var code=value.Trim().Split('.')[0];if(code.Length!=6||code.Any(ch=>!char.IsAsciiDigit(ch)))throw new ArgumentException("行情代码必须为六位数字");return code;}).Distinct().ToArray();}
    [HttpGet("market/history")]
    public Task<IActionResult> History(string symbol,CancellationToken cancellation,bool refresh=false)
    {if(symbol is not ("000300" or "000001" or "399001" or "399006"))throw new ArgumentException("指数代码无效");return ReadPublic("market.history",new{symbol},cancellation,refresh);}
    [HttpGet("market/quotes")]
    public async Task<IActionResult> Quotes([FromQuery]string[]? stocks,[FromQuery]string[]? indices,CancellationToken cancellation)
    {if((stocks?.Length??0)+(indices?.Length??0)>20)throw new ArgumentException("行情标的最多20个");var selectedStocks=Codes(stocks);var selectedIndices=Codes(indices);var result=await publicData.QuotesAsync(selectedStocks,selectedIndices,cancellation);if(result["unavailable"]!.AsArray().Count>0)await compute.QueuePublicRefresh(Id,"market.quotes",new{stocks=selectedStocks,indices=selectedIndices},cancellation);PublicDataQuality.Apply("market.quotes",result,DateTimeOffset.UtcNow);return Ok(result);}
    [HttpGet("market/news")]
    public async Task<IActionResult> News(int limit=30,CancellationToken cancellation=default)
    {if(limit is <1 or >50)throw new ArgumentException("新闻条数须为1至50");var data=await publicData.NewsAsync(limit,cancellation);if(data is null){await compute.QueuePublicRefresh(Id,"market.news",new{limit=50},cancellation);throw new QuantAssistant.Core.Errors.ApplicationFailure("public_snapshot_unavailable",503,"资讯快照正在准备，请稍后刷新。");}return Ok(data);}
    [HttpGet("funds/{symbol}/detail")]
    public Task<IActionResult> Detail(string symbol,CancellationToken cancellation,bool refresh=false)=>ReadPublic("fund.detail",new{symbol=FundFeeRepository.Symbol(symbol)},cancellation,refresh);
    [HttpGet("funds/valuations")]
    public async Task<IActionResult> Valuations([FromQuery]string[]? symbols,CancellationToken cancellation,bool separateNav=false)
    {
        if(symbols?.Length>100)throw new ArgumentException("基金代码过多");
        var selected=(symbols is {Length:>0}?symbols:(await ledger.ListPositionsAsync(Id,cancellation)).Where(p=>p.AssetType==AssetType.Fund&&p.Quantity>0).Select(p=>p.Symbol).ToArray()).Select(FundFeeRepository.Symbol).Distinct().ToArray();
        if(selected.Length>20)throw new ArgumentException("基金估值最多20个");
        var result=await publicData.ValuationsAsync(selected,cancellation);
        if(result["unavailable"]!.AsArray().Count>0)await compute.QueuePublicRefresh(Id,"fund.valuations",new{symbols=selected},cancellation);
        PublicDataQuality.Apply("fund.valuations",result,DateTimeOffset.UtcNow,separateNav);
        return Ok(result);
    }
}
