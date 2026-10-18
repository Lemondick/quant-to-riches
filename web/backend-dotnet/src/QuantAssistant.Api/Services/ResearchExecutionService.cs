using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

public sealed partial class ResearchExecutionService(AccountDatabase database,ModelConnectionService connections,ComputeService compute)
{
    private const string Prompt="bull-bear-review-v1",Adapter="price-volume-review-v1",Upstream="926de6e6308047e02c4d8110838fd2d2125dceb6";
    [GeneratedRegex("^[a-f0-9]{32}$")] private static partial Regex IdPattern();
    [GeneratedRegex(@"^\d{6}\.(SH|SZ)$")] private static partial Regex StockPattern();
    private static ApplicationFailure Failure(string code,int status=409)=>new(code,status,code switch {"preview_not_found"=>"未找到预览，请重新预览选股。","preview_expired"=>"预览已过期，请重新预览选股。","not_candidate"=>"该股票不属于本次预览候选。","ineligible_evidence"=>"研究证据未通过校验。","preview_changed"=>"行情或来源证据已变化，请重新预览选股。","job_not_found"=>"未找到研究任务。","job_active"=>"任务仍在执行，不能重复研究。",_=>"研究任务未通过校验。"});
    private static JsonObject Snapshot(AccountSql sql,string user,string snapshot)=>sql.Query("SELECT snapshot_id,content_hash,payload_json,created_at,expires_at FROM agent_preview_snapshots WHERE user_id=$1 AND snapshot_id=$2",user,snapshot).FirstOrDefault()?.AsObject()??throw Failure("preview_not_found",404);
    private static void Eligible(JsonObject snapshot,string symbol)
    {
        if(DateTimeOffset.Parse(snapshot["expires_at"]!.GetValue<string>())<=DateTimeOffset.UtcNow) throw Failure("preview_expired");
        var payload=snapshot["payload_json"]!.AsObject();
        if(!payload["candidates"]!.AsArray().Any(item=>item!.GetValue<string>()==symbol)) throw Failure("not_candidate",422);
        if(payload["blockers"]!.AsArray().Count>0) throw Failure("ineligible_evidence",422);
    }
    private static JsonNode Sorted(JsonNode node)=>node is JsonObject obj ? new JsonObject(obj.OrderBy(p=>p.Key,StringComparer.Ordinal).Select(p=>new KeyValuePair<string,JsonNode?>(p.Key,p.Value is null?null:Sorted(p.Value)))) : node is JsonArray array ? new JsonArray(array.Select(item=>item is null?null:Sorted(item)).ToArray()):node.DeepClone();
    private static string Fingerprint(JsonObject value)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Sorted(value).ToJsonString(new JsonSerializerOptions{Encoder=JavaScriptEncoder.UnsafeRelaxedJsonEscaping})))).ToLowerInvariant();

    public async Task<(JsonObject Result,bool Reused)> Create(string user,string snapshotId,string symbol,CancellationToken cancellation,string? retryId=null)
    {
        if(!IdPattern().IsMatch(snapshotId)||!StockPattern().IsMatch(symbol)) throw new ArgumentException("预览标识或股票代码无效");
        var snapshot=database.Read(sql=>Snapshot(sql,user,snapshotId));Eligible(snapshot,symbol);
        var current=(await compute.Run(user,"strategy.preview",new {parameters=snapshot["payload_json"]!["parameters"]},cancellation,accountSnapshot:true))!.AsObject();
        if(current["_evidence"]?["content_hash"]?.GetValue<string>()!=snapshot["content_hash"]!.GetValue<string>()) throw Failure("preview_changed");
        var (jobId,reused)=database.Write(user,sql=>
        {
            snapshot=Snapshot(sql,user,snapshotId);Eligible(snapshot,symbol);
            if(retryId is not null)
            {
                var old=sql.Query("SELECT status FROM agent_research_jobs WHERE user_id=$1 AND job_id=$2",user,retryId).FirstOrDefault()??throw Failure("job_not_found",404);
                if(old["status"]!.GetValue<string>() is "queued" or "running" or "cancel_requested") throw Failure("job_active");
            }
            var config=connections.Config(user);if(!config["configured"]!.GetValue<bool>()) throw new ApplicationFailure("model_not_configured",503,"请先完成模型连接和选择。");
            var endpoint=connections.Read(user)["provider"]!["endpoint"]!.GetValue<string>();var models=config["models"]!.AsObject();
            var hash=Fingerprint(new() { ["input"]=snapshot["content_hash"]!.DeepClone(),["symbol"]=symbol,["config"]=new JsonObject{["provider"]="compatible",["model"]=models["strategy"]!.DeepClone(),["endpoint"]=endpoint},["prompt"]=Prompt,["adapter"]=Adapter,["upstream"]=Upstream });
            var existing=sql.Query("SELECT job_id,attempt_no FROM agent_research_jobs WHERE user_id=$1 AND cache_key=$2",user,hash).FirstOrDefault();
            if(existing is not null && (retryId is null || existing["job_id"]!.GetValue<string>()!=retryId)) return (existing["job_id"]!.GetValue<string>(),true);
            if((long)sql.Scalar("SELECT count(*) FROM agent_research_attempts WHERE user_id=$1 AND quota_date=$2",user,FundFeeRepository.Today())!>=10) throw new ApplicationFailure("quota_exceeded",429,"今日研究次数已用完，失败和重试也计入额度。");
            var id=existing?["job_id"]?.GetValue<string>()??Guid.NewGuid().ToString("N");var attempt=existing is null?1:existing["attempt_no"]!.GetValue<int>()+1;
            sql.Execute("INSERT INTO agent_research_jobs(user_id,job_id,cache_key,symbol,status,attempt_no,phase,deadline,error_code,created_at) VALUES($1,$2,$3,$4,'queued',$5,'queued',$6,NULL,now()) ON CONFLICT(user_id,job_id) DO UPDATE SET status='queued',attempt_no=EXCLUDED.attempt_no,phase='queued',deadline=EXCLUDED.deadline,error_code=NULL",user,id,hash,symbol,attempt,DateTime.UtcNow.AddMinutes(3));
            sql.Execute("INSERT INTO agent_research_attempts(user_id,job_id,attempt_no,snapshot_id,quota_date,started_at,execution_status,provider,model,prompt_version,upstream_commit) VALUES($1,$2,$3,$4,$5,now(),'queued','compatible',$6,$7,$8)",user,id,attempt,snapshotId,FundFeeRepository.Today(),models["strategy"]!.GetValue<string>(),Prompt,Upstream);
            var evidence=snapshot["payload_json"]!.AsObject();
            var request=new JsonObject{["symbol"]=symbol,["data_date"]=evidence["data_date"]!.DeepClone(),["facts"]=evidence["facts"]![symbol]!.DeepClone(),["warnings"]=evidence["warnings"]!.DeepClone(),["screening_rules"]=new JsonObject{["min_amount_20"]=evidence["parameters"]!["min_amount_20"]!.DeepClone(),["top_candidate_count"]=evidence["parameters"]!["top_candidate_count"]!.DeepClone(),["min_listing_days"]=250}};
            ComputeJobRepository.EnqueueOwned(sql,user,"agent.research",new JsonObject{["public_id"]=id,["public_attempt"]=attempt,["models"]=models.DeepClone(),["request"]=request,
                ["snapshot"]=AnalysisSnapshotRepository.Create(sql,user),["preview_parameters"]=evidence["parameters"]!.DeepClone(),["preview_content_hash"]=snapshot["content_hash"]!.DeepClone(),["preview_expires_at"]=snapshot["expires_at"]!.DeepClone()}.ToJsonString(),DateTime.UtcNow.AddMinutes(3));
            return (id,false);
        });
        var result=Get(user,jobId);result["cache_hit"]=reused;return(result,reused);
    }
    public JsonObject Get(string user,string id)=>database.Read(sql=>
    {
        var row=sql.Query("SELECT j.job_id,j.symbol,j.status,j.attempt_no,j.phase,j.deadline,j.error_code,j.created_at,a.snapshot_id,a.started_at,a.provider,a.model,a.prompt_version,a.upstream_commit,a.report_json,a.usage_json,a.completed_at FROM agent_research_jobs j JOIN agent_research_attempts a ON a.user_id=j.user_id AND a.job_id=j.job_id AND a.attempt_no=j.attempt_no WHERE j.user_id=$1 AND j.job_id=$2",user,id).FirstOrDefault()?.AsObject()??throw Failure("job_not_found",404);
        var snapshot=Snapshot(sql,user,row["snapshot_id"]!.GetValue<string>());var payload=snapshot["payload_json"]!.AsObject();var symbol=row["symbol"]!.GetValue<string>();
        row["report"]=row["report_json"]?.DeepClone();row.Remove("report_json");row["usage"]=row["usage_json"]?.DeepClone();row.Remove("usage_json");
        foreach(var key in new[]{"data_date","sources","source_declarations","warnings"}) row[key]=payload[key]?.DeepClone();
        row["facts"]=payload["facts"]?[symbol]?.DeepClone();row["input_fingerprint"]=snapshot["content_hash"]!.DeepClone();row["snapshot_expires_at"]=snapshot["expires_at"]!.DeepClone();
        row["expired"]=DateTimeOffset.Parse(snapshot["expires_at"]!.GetValue<string>())<=DateTimeOffset.UtcNow;row["simulated"]=row["provider"]!.GetValue<string>()=="mock";row["estimated_cost"]=null;row["error_message"]=row["error_code"] is null?null:"本次研究未完成，请查看错误码后显式重试。";row["research_only"]=true;row["cache_hit"]=false;
        return row;
    });
    public JsonObject Cancel(string user,string id)
    {
        database.Write(user,sql=>
        {
            var job=sql.Query("SELECT status,attempt_no FROM agent_research_jobs WHERE user_id=$1 AND job_id=$2 FOR UPDATE",user,id).FirstOrDefault()??throw Failure("job_not_found",404);
            var status=job["status"]!.GetValue<string>();if(status is not("queued" or "running")) return 0;
            sql.Execute("UPDATE agent_research_jobs SET status=$3,phase=$3,error_code=CASE WHEN $3='cancelled' THEN 'cancelled' ELSE NULL END WHERE user_id=$1 AND job_id=$2",user,id,status=="queued"?"cancelled":"cancel_requested");
            if(status=="queued") sql.Execute("UPDATE agent_research_attempts SET execution_status='cancelled',completed_at=now() WHERE user_id=$1 AND job_id=$2 AND attempt_no=$3",user,id,job["attempt_no"]!.GetValue<int>());
            return sql.Execute("UPDATE compute_jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END,error_code='cancelled' WHERE user_id=$1 AND kind='agent.research' AND input_json->>'public_id'=$2 AND status IN('queued','running')",user,id);
        });return Get(user,id);
    }
}
