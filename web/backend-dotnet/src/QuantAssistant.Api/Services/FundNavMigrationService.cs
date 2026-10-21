using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

public sealed class FundNavMigrationService(AccountDatabase database,ComputeService compute,IConfiguration configuration)
{
    private static decimal Amount(JsonNode row,string key)=>decimal.Parse(row[key]!.GetValue<string>(),CultureInfo.InvariantCulture);
    private static string Money(decimal amount)=>amount.ToString(CultureInfo.InvariantCulture);
    private static DateOnly Day(JsonNode row,string key)=>DateOnly.Parse(row[key]!.GetValue<string>());
    private static DateTimeOffset Stamp(JsonNode row,string key)=>DateTimeOffset.Parse(row[key]!.GetValue<string>());
    private static JsonObject Blocked(DateOnly day,JsonArray reasons,JsonArray? sources=null)=>new(){["status"]="blocked",["as_of_date"]=day.ToString("yyyy-MM-dd"),["preview_id"]=null,["point"]=null,["blockers"]=reasons,["sources"]=sources??new()};
    private static JsonObject Point(JsonObject row)
    {var result=new JsonObject();foreach(var key in new[]{"run_id","as_of_date","generated_at","total_value","cash","unit_nav","high_water_mark","net_external_flow","is_baseline"})result[key]=row[key]?.DeepClone();return result;}
    public async Task<JsonObject> Run(string user,DateOnly day,bool confirmed,string? previewId,CancellationToken cancellation)
    {
        if(day>FundFeeRepository.Today())return Blocked(day,new("不能使用未来日期净值"));
        var input=database.Read(sql=>new JsonObject{["financial"]=FinancialSnapshot.Read(sql,user),["snapshot"]=AnalysisSnapshotRepository.Create(sql,user)});
        var state=input["financial"]!.AsObject();var positions=state["positions"]!.AsArray();var valuations=state["portfolio_valuations"]!.AsArray();
        if(valuations.FirstOrDefault(v=>Day(v!,"as_of_date")==day) is JsonObject existing)return new(){["status"]="existing",["as_of_date"]=day.ToString("yyyy-MM-dd"),["preview_id"]=null,["point"]=Point(existing),["blockers"]=new JsonArray(),["sources"]=new JsonArray()};
        if(positions.Count==0||positions.Any(p=>Day(p!,"as_of_date")>day||p!["asset_type"]!.GetValue<string>() is not("fund" or "cash"))||valuations.Any(v=>Day(v!,"as_of_date")>=day))return Blocked(day,new("需先确认完整基金与现金账户，且不得倒序追加净值"));
        var symbols=positions.OfType<JsonObject>().Where(p=>p["asset_type"]!.GetValue<string>()=="fund"&&Amount(p,"quantity")>0).Select(p=>p["symbol"]!.GetValue<string>()).ToArray();
        if(symbols.Length is <1 or >20)return Blocked(day,new("需有1至20只已确认的基金持仓"));
        var collected=(await compute.Run(user,"fund.nav_sources",new{symbols,as_of_date=day,snapshot=input["snapshot"]},cancellation,seconds:300))!.AsObject();var sources=collected["sources"]!.AsArray();
        if(collected["blockers"]!.AsArray().Count>0)return Blocked(day,collected["blockers"]!.DeepClone().AsArray(),sources.DeepClone().AsArray());
        var prices=sources.OfType<JsonObject>().ToDictionary(s=>s["symbol"]!.GetValue<string>(),s=>Amount(s,"unit_nav"));
        if(prices.Count!=symbols.Length||prices.Values.Any(value=>value<=0))throw new ArgumentException("正式净值来源不完整");
        var cashRows=positions.OfType<JsonObject>().Where(p=>p["asset_type"]!.GetValue<string>()=="cash").ToArray();
        if(cashRows.Length!=1||Amount(cashRows[0],"market_value")<0)return Blocked(day,new("需有且仅有一个非负现金持仓"));
        var cash=Amount(cashRows[0],"market_value");var total=cash+positions.OfType<JsonObject>().Where(p=>p["asset_type"]!.GetValue<string>()=="fund").Sum(p=>Amount(p,"quantity")*prices.GetValueOrDefault(p["symbol"]!.GetValue<string>()));
        if(total<=0)return Blocked(day,new("组合总资产必须为正"));
        var events=state["portfolio_events"]!.AsArray().OfType<JsonObject>().ToArray();var previous=valuations.OfType<JsonObject>().OrderByDescending(v=>Day(v,"as_of_date")).ThenByDescending(v=>Stamp(v,"generated_at")).FirstOrDefault();
        var baseline=events.Where(e=>e["event_type"]!.GetValue<string>()=="baseline"&&e["status"]!.GetValue<string>()=="confirmed"&&Day(e,"effective_date")==day).OrderByDescending(e=>Stamp(e,"recorded_at")).FirstOrDefault();
        var flows=events.Where(e=>e["status"]!.GetValue<string>()=="confirmed"&&e["event_type"]!.GetValue<string>() is "deposit" or "withdrawal" or "calibration"&&Day(e,"effective_date")<=day&&(previous is null||Day(e,"effective_date")>Day(previous,"as_of_date"))).ToArray();
        var blockers=new JsonArray();
        if(events.Any(e=>e["status"]!.GetValue<string>()=="pending"&&Day(e,"effective_date")<=day))blockers.Add("存在待确认组合事件");
        var boundary=previous is null?baseline is null?DateTimeOffset.MinValue:Stamp(baseline,"recorded_at"):Stamp(previous,"generated_at");
        if(previous is null&&baseline is null)blockers.Add("组合净值尚未显式确认初始基线");
        var transactions=state["transactions"]!.AsArray();var manual=positions.OfType<JsonObject>().Where(p=>!transactions.Any(t=>Stamp(t!,"recorded_at")==Stamp(p,"updated_at"))&&!events.Any(e=>e["event_type"]!.GetValue<string>() is "deposit" or "withdrawal" or "calibration"&&Stamp(e,"recorded_at")==Stamp(p,"updated_at"))&&!new[]{"dividend_events","corporate_action_events"}.Any(table=>state[table]!.AsArray().Any(e=>Stamp(e!,"recorded_at")==Stamp(p,"updated_at")))).Select(p=>Stamp(p,"updated_at"));
        if(manual.Any(date=>date>boundary)||state["audit_log"]!.AsArray().Any(a=>a!["entity_type"]!.GetValue<string>()=="position"&&Stamp(a,"changed_at")>boundary))blockers.Add("存在未对账的持仓覆盖，净值序列已冻结");
        if(previous is not null&&events.Any(e=>e["status"]!.GetValue<string>()=="confirmed"&&Day(e,"effective_date")<=Day(previous,"as_of_date")&&Stamp(e,"recorded_at")>boundary))blockers.Add("存在晚到组合事件，请先核对历史");
        var flow=flows.Sum(e=>e["amount"] is null?0:Amount(e,"amount"));
        if(flows.Any(e=>e["amount"] is null||e["event_type"]!.GetValue<string>()=="deposit"&&Amount(e,"amount")<=0||e["event_type"]!.GetValue<string>()=="withdrawal"&&Amount(e,"amount")>=0)||total-flow<=0)blockers.Add("外部资金流金额或方向无效");
        if(blockers.Count>0)return Blocked(day,blockers,sources.DeepClone().AsArray());
        var nav=previous is null?1:Amount(previous,"unit_nav")*(total-flow)/Amount(previous,"total_value");var high=previous is null?1:Math.Max(Amount(previous,"high_water_mark"),nav);
        nav=decimal.Round(nav,12,MidpointRounding.AwayFromZero);high=decimal.Round(high,12,MidpointRounding.AwayFromZero);total=decimal.Round(total,8,MidpointRounding.AwayFromZero);
        var hash=FinancialSnapshot.Hash(new JsonObject{["state"]=state.DeepClone(),["as_of_date"]=day.ToString("yyyy-MM-dd"),["prices"]=new JsonObject(prices.OrderBy(p=>p.Key,StringComparer.Ordinal).Select(p=>new KeyValuePair<string,JsonNode?>(p.Key,JsonValue.Create(Money(p.Value))))) });
        var run="fund-nav-"+hash[..32];var point=new JsonObject{["run_id"]=run,["as_of_date"]=day.ToString("yyyy-MM-dd"),["generated_at"]=DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(8)).ToString("O"),["total_value"]=Money(total),["cash"]=Money(cash),["unit_nav"]=Money(nav),["high_water_mark"]=Money(high),["net_external_flow"]=Money(flow),["is_baseline"]=previous is null};
        if(confirmed)
        {
            if(previewId!=hash)return Blocked(day,new("账户或净值已变更，请重新预览并核对"),sources.DeepClone().AsArray());
            database.WriteNonLedger(user,sql=>
            {
                if(FinancialSnapshot.Hash(FinancialSnapshot.Read(sql,user))!=FinancialSnapshot.Hash(state))throw new ApplicationFailure("nav_preview_changed",409,"账户已变化，请重新预览。");
                long snapshotId=0;
                foreach(var source in sources.OfType<JsonObject>())
                {
                    var path=Path.GetFullPath(source["snapshot_path"]!.GetValue<string>());var root=Path.GetFullPath(input["snapshot"]!["role"]!.GetValue<string>()=="admin"?configuration["QUANT_SITE_ROOT"]!:Path.Combine(configuration["QUANT_SITE_ROOT"]!,"users",user))+Path.DirectorySeparatorChar;
                    var comparison=OperatingSystem.IsWindows()?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal;
                    NativePaths.RejectReparseAncestors(Path.GetDirectoryName(path)!);
                    if(!path.StartsWith(root,comparison)||!File.Exists(path)||(File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new ArgumentException("净值来源文件无效");
                    using(var file=File.OpenRead(path))
                        if(Convert.ToHexString(SHA256.HashData(file)).ToLowerInvariant()!=source["snapshot_file_sha256"]!.GetValue<string>())
                            throw new ApplicationFailure("nav_source_changed",409,"净值来源文件已变化，请重新预览。");
                    snapshotId=Convert.ToInt64(sql.Scalar("INSERT INTO data_snapshots(user_id,dataset,source,as_of_date,fetched_at,path,sha256,row_count,status,issues_json) VALUES($1,'fund_nav',$2,$3,$4,$5,$6,1,'valid','[]') ON CONFLICT(user_id,dataset,source,as_of_date,sha256) DO UPDATE SET path=EXCLUDED.path RETURNING id",user,source["source"]!.GetValue<string>(),day,Stamp(source,"observed_at").UtcDateTime,path,source["snapshot_sha256"]!.GetValue<string>()));
                }
                sql.Execute("INSERT INTO portfolio_valuations(user_id,run_id,as_of_date,generated_at,data_snapshot_id,total_value,cash,unit_nav,high_water_mark,net_external_flow,previous_run_id,positions_fingerprint,events_fingerprint,is_baseline) VALUES($1,$2,$3,now(),$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",user,run,day,snapshotId,total,cash,nav,high,flow,previous?["run_id"]?.GetValue<string>(),FinancialSnapshot.Hash(positions),FinancialSnapshot.Hash(new JsonArray(flows.Select(f=>f.DeepClone()).ToArray())),previous is null);return 0;
            });
        }
        return new(){["status"]=confirmed?"recorded":"ready",["as_of_date"]=day.ToString("yyyy-MM-dd"),["preview_id"]=hash,["point"]=point,["blockers"]=new JsonArray(),["sources"]=sources.DeepClone().AsArray()};
    }
}
