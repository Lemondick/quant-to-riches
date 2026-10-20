using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using QuantAssistant.Core.Market;
using System.Text.Json.Nodes;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1")]
public sealed class MarketController(ComputeService compute,ILedgerRepository ledger,PublicMarketSnapshot snapshots,PublicSnapshotRepository publicData,TradingCalendarRepository calendars):ControllerBase
{
    private string Id=>User.FindFirst("sub")!.Value;
    private async Task<IActionResult> ReadPublic(string kind,object input,CancellationToken cancellation,bool refresh)
    { var result=await compute.ReadPublic(Id,kind,input,cancellation,refresh);if(result.Data is System.Text.Json.Nodes.JsonObject data)PublicDataQuality.Apply(kind,data,DateTimeOffset.UtcNow,calendars:calendars.CurrentSnapshot);Response.Headers["X-Public-Cache"]=result.Cache;return Ok(result.Data); }
    [HttpGet("market/snapshot")]
    public Task<IActionResult> Snapshot(string? section,CancellationToken cancellation,bool refresh=false,bool fullCatalog=false)
    {var result=snapshots.Read(section,refresh);if(!fullCatalog && result.Data is System.Text.Json.Nodes.JsonObject data)PublicMarketSnapshot.UseLegacySectors(data);Response.Headers["X-Public-Cache"]=result.Cache;return Task.FromResult<IActionResult>(Ok(result.Data));}
    private static string[] Codes(string[]? values)
    {if(values?.Length>50)throw new ArgumentException("行情标的过多");return (values??[]).Select(value=>{var code=value.Trim().Split('.')[0];if(code.Length!=6||code.Any(ch=>!char.IsAsciiDigit(ch)))throw new ArgumentException("行情代码必须为六位数字");return code;}).Distinct().ToArray();}
    [HttpGet("market/history")]
    public async Task<IActionResult> History(string? symbol,CancellationToken cancellation,bool refresh=false,[FromQuery(Name="index_id")]string? indexId=null)
    {
        var definition=indexId is not null?IndexRegistry.Get(indexId):symbol is not null?IndexRegistry.ResolveLegacy(symbol):throw new ArgumentException("请选择指数");
        if(symbol is not null && symbol!=definition.ExchangeCode)throw new ArgumentException("指数标识与代码不一致");
        var identity=IndexRegistry.JsonDefinitions().OfType<JsonObject>().Single(row=>row["id"]!.GetValue<string>()==definition.Id).DeepClone();
        if(!definition.Enabled || definition.History?.VerificationStatus!="verified")
            return Ok(new JsonObject{["id"]=definition.Id,["index_id"]=definition.Id,["symbol"]=definition.ExchangeCode,["name"]=definition.Name,["market"]=definition.Market,["currency"]=definition.Currency,["index_definition"]=identity,["items"]=new JsonArray(),["quality_status"]="unavailable",["quality_reason"]="history_not_connected",["source_time"]=null,["fetched_at"]=null,["stale"]=false,["unavailable"]=new JsonArray("history")});
        // Existing four inputs remain byte compatible; other series have independent stable-ID inputs.
        var input=definition.LegacyHistorySymbol is { } legacy?new JsonObject{["symbol"]=legacy}:new JsonObject{["index_id"]=definition.Id};
        var result=await compute.ReadPublic(Id,"market.history",input,cancellation,refresh);
        var data=result.Data?.AsObject()??throw new QuantAssistant.Core.Errors.ApplicationFailure("public_snapshot_unavailable",503,"指数历史暂不可用。");
        async Task<IActionResult> Reprepare()
        {
            compute.DiscardPublic(Id,"market.history",input);
            await compute.QueuePublicRefresh(Id,"market.history",input,cancellation,force:true);
            throw new QuantAssistant.Core.Errors.ApplicationFailure("public_snapshot_unavailable",503,"指数历史未通过校验，正在重新准备。");
        }
        bool Match(string key,string expected,bool required=false)=>data[key] is JsonValue value&&value.TryGetValue<string>(out var text)&&text==expected || !required&&!data.ContainsKey(key);
        var strict=definition.LegacyHistorySymbol is null;
        if(!Match("symbol",definition.ExchangeCode,true)||!Match("id",definition.Id,strict)||!Match("market",definition.Market,strict)||!Match("currency",definition.Currency,strict)||!Match("source_symbol",definition.History.Symbol,strict)||!Match("source",definition.History.Provider,strict))
            return await Reprepare();
        data["id"]=definition.Id;data["index_id"]=definition.Id;data["name"]=definition.Name;data["market"]=definition.Market;data["currency"]=definition.Currency;data["calendar_id"]=definition.CalendarId;data["index_definition"]=identity;
        PublicDataQuality.Apply("market.history",data,DateTimeOffset.UtcNow,calendars:calendars?.CurrentSnapshot);
        if(data["items"] is not JsonArray {Count:>0} || data["quality_status"]?.GetValue<string>() is "invalid" or "unavailable") return await Reprepare();
        Response.Headers["X-Public-Cache"]=result.Cache;return Ok(data);
    }
    [HttpGet("market/quotes")]
    public async Task<IActionResult> Quotes([FromQuery]string[]? stocks,[FromQuery]string[]? indices,CancellationToken cancellation)
    {if((stocks?.Length??0)+(indices?.Length??0)>20)throw new ArgumentException("行情标的最多20个");var selectedStocks=Codes(stocks);var selectedIndices=Codes(indices);foreach(var code in selectedIndices)IndexRegistry.ResolveLegacy(code);var result=await publicData.QuotesAsync(selectedStocks,selectedIndices,cancellation);if(result["unavailable"]!.AsArray().Count>0)await compute.QueuePublicRefresh(Id,"market.quotes",new{stocks=selectedStocks,indices=selectedIndices},cancellation);PublicDataQuality.Apply("market.quotes",result,DateTimeOffset.UtcNow,calendars:calendars.CurrentSnapshot);return Ok(result);}
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
        PublicDataQuality.Apply("fund.valuations",result,DateTimeOffset.UtcNow,separateNav,calendars.CurrentSnapshot);
        return Ok(result);
    }
}
