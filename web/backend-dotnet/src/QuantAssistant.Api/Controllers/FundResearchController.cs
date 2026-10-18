using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1/research/funds")]
public sealed partial class FundResearchController(AccountDatabase database,ComputeService compute):ControllerBase
{
    public sealed record RefreshBody(string[] Symbols);
    [GeneratedRegex("^[a-f0-9]{32}$")]private static partial Regex Identity();
    private string Id=>User.FindFirst("sub")!.Value;
    private JsonObject Input(JsonObject? extra=null)=>database.Read(sql=>
    {extra??=new();extra["pointer"]=sql.Scalar("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key='study-pointer'",Id) is string raw?JsonNode.Parse(raw):null;return extra;});
    [HttpGet("advisor-tracking")]
    public async Task<IActionResult> Tracking(int limit=30,int page=1,CancellationToken cancellation=default)
    {if(limit is <1 or >50 || page is <1 or >10000)throw new ArgumentException("分页参数无效");return Ok(await compute.Run(Id,"fund.tracking",new{limit,page},cancellation,accountSnapshot:true));}
    [HttpGet("portfolio-risk")]public async Task<IActionResult> Risk(CancellationToken cancellation)=>Ok(await compute.Run(Id,"fund.risk",new{},cancellation,accountSnapshot:true));
    [HttpPost("public-observations")]
    public async Task<IActionResult> Refresh(RefreshBody body,CancellationToken cancellation)
    {
        if(body.Symbols.Length is <1 or >6)throw new ArgumentException("每次更新1至6只基金");
        var symbols=body.Symbols.Select(FundFeeRepository.Symbol).Distinct().ToArray();
        var result=(await compute.Run(Id,"fund.public_observations",new{symbols},cancellation,accountSnapshot:true,seconds:90))!.AsObject();
        if(result.Remove("_snapshots",out var rows)&&rows is JsonArray snapshots) database.Write(Id,sql=>
        {
            foreach(var row in snapshots.OfType<JsonObject>())
            {
                var raw=row["snapshot_json"]!.GetValue<string>();var hash=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(raw))).ToLowerInvariant();var symbol=row["symbol"]!.GetValue<string>();
                if(hash!=row["fingerprint"]!.GetValue<string>() || FundFeeRepository.Symbol(symbol)!=symbol)throw new ArgumentException("研究观察校验失败");
                sql.Execute("INSERT INTO fund_research_snapshots(user_id,fingerprint,symbol,observed_at,snapshot_json) VALUES($1,$2,$3,$4,$5::json) ON CONFLICT(user_id,fingerprint) DO NOTHING",Id,hash,symbol,DateTimeOffset.Parse(row["observed_at"]!.GetValue<string>()).UtcDateTime,raw);
            }return 0;
        });return Ok(result);
    }
    [HttpGet("cost-study")]public async Task<IActionResult> Study(CancellationToken cancellation)=>Ok(await compute.Run(Id,"fund.study",Input(),cancellation,accountSnapshot:true));
    [HttpGet("cost-study/curves")]
    public async Task<IActionResult> Curves(string symbol,string period,[FromQuery(Name="purchase_rate")]string rate,string version,CancellationToken cancellation)
    {if(FundFeeRepository.Symbol(symbol)!=symbol || period is not("full" or "early" or "late") || rate is not("0" or "0.0012" or "0.012") || version.Length is <1 or >64)throw new ArgumentException("曲线参数无效");return Ok(await compute.Run(Id,"fund.curves",Input(new(){["symbol"]=symbol,["period"]=period,["purchase_rate"]=rate,["version"]=version}),cancellation,accountSnapshot:true));}
    [HttpGet("cost-study/update")]
    public IActionResult UpdateStatus()=>Ok(database.Read(sql=>
    {
        var row=sql.Query("SELECT job_id,status,result_json,error_code,created_at,completed_at FROM compute_jobs WHERE user_id=$1 AND kind='fund.study_update' ORDER BY created_at DESC LIMIT 1",Id).FirstOrDefault()?.AsObject();
        if(row is null)return new JsonObject{["status"]="idle",["message"]="尚未发起研究更新。",["model_calls"]=0};
        if(row["status"]!.GetValue<string>()=="completed") {var result=row["result_json"]!.AsObject();result.Remove("pointer");return result.DeepClone().AsObject();}
        row["status"]=row["status"]!.GetValue<string>() is "queued" or "running"?"running":"failed";row.Remove("result_json");row["model_calls"]=0;row["message"]=row["status"]!.GetValue<string>()=="running"?"更新已开始；完成校验前继续展示旧版。":"更新未完成，旧版保留；请显式重试。";return row.AsObject();
    }));
    [HttpPost("cost-study/update")]
    public IActionResult Update()=>Accepted(database.Write(Id,sql=>
    {
        var active=sql.Scalar("SELECT job_id FROM compute_jobs WHERE user_id=$1 AND kind='fund.study_update' AND status IN('queued','running') LIMIT 1",Id) as string;
        var id=active;
        if(id is null)
        {
            var input=new JsonObject{["snapshot"]=AnalysisSnapshotRepository.Create(sql,Id),["study_id"]=Guid.NewGuid().ToString("N"),["pointer"]=sql.Scalar("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key='study-pointer'",Id) is string raw?JsonNode.Parse(raw):null};
            id=ComputeJobRepository.EnqueueOwned(sql,Id,"fund.study_update",input.ToJsonString(),DateTime.UtcNow.AddHours(1));
        }
        return new JsonObject{["job_id"]=id,["status"]="running",["model_calls"]=0,["message"]="更新已开始；完成校验前继续展示旧版。"};
    }));
}
