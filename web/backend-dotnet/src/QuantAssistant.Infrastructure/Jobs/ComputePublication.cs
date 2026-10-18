using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Jobs;

/// <summary>Lease validation and public task state changes commit in the same transaction.</summary>
public static class ComputePublication
{
    public static void Started(AccountSql sql, string user, string kind, string input) => StartedAsync(sql, user, kind, input).GetAwaiter().GetResult();

    public static async Task StartedAsync(AccountSql sql, string user, string kind, string input)
    {
        var id = JsonNode.Parse(input)?["public_id"]?.GetValue<string>();
        if (id is null) return;
        if (kind == "advisor.report") await sql.ExecuteAsync("UPDATE market_advisor_reports SET status='running',updated_at=now() WHERE user_id=$1 AND report_id=$2 AND status='queued'",user,id);
        if (kind == "agent.research")
        {
            await sql.ExecuteAsync("UPDATE agent_research_jobs SET status='running',phase='research' WHERE user_id=$1 AND job_id=$2 AND status='queued'",user,id);
            await sql.ExecuteAsync("UPDATE agent_research_attempts a SET execution_status='running' FROM agent_research_jobs j WHERE a.user_id=$1 AND a.job_id=$2 AND j.user_id=a.user_id AND j.job_id=a.job_id AND a.attempt_no=j.attempt_no AND a.execution_status='queued'",user,id);
        }
    }

    public static void Finished(AccountSql sql,string user,string computeId,string kind,string input,string status,string? raw,string? error) => FinishedAsync(sql, user, computeId, kind, input, status, raw, error).GetAwaiter().GetResult();

    public static async Task FinishedAsync(AccountSql sql,string user,string computeId,string kind,string input,string status,string? raw,string? error)
    {
        var payload = JsonNode.Parse(input)!.AsObject(); var id = payload["public_id"]?.GetValue<string>();
        if(kind=="fund.study_update" && status=="completed" && raw is not null && JsonNode.Parse(raw) is JsonObject study && study["status"]?.GetValue<string>()=="completed")
        {
            var pointer=study["pointer"]!.AsObject();
            if(pointer["version"]!.GetValue<string>()!=payload["study_id"]!.GetValue<string>()) throw new ArgumentException("研究版本标识无效");
            await sql.ExecuteAsync("INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,'study-pointer',1,$2::jsonb) ON CONFLICT(user_id,document_key) DO UPDATE SET revision=user_documents.revision+1,payload=EXCLUDED.payload,updated_at=now()",user,pointer.ToJsonString());
        }
        if (id is null) return;
        var result = raw is null ? null : JsonNode.Parse(raw)?.AsObject();
        var success = status == "completed";
        if (kind == "advisor.report")
        {
            var reportStatus = success ? result?["status"]?.GetValue<string>() : "failed";
            if (reportStatus is not ("completed" or "blocked" or "failed")) throw new ArgumentException("报告结果无效");
            var code = success ? result?["error_code"]?.GetValue<string>() : error;
            await sql.ExecuteAsync("UPDATE market_advisor_reports SET status=$3,report_json=$4::json,input_json=$5::json,progress_json=$6::json,error_code=$7,updated_at=now() WHERE user_id=$1 AND report_id=$2 AND status IN('queued','running')",user,id,reportStatus,result?["report"]?.ToJsonString(),result?["input"]?.ToJsonString(),result?["progress"]?.ToJsonString(),code);
            if (result?["decision_snapshot"] is JsonObject seal)
                await sql.ExecuteAsync("INSERT INTO advisor_decision_snapshots(user_id,report_id,captured_at,snapshot_json,fingerprint) VALUES($1,$2,now(),$3::json,$4) ON CONFLICT(user_id,report_id) DO NOTHING",user,id,seal["snapshot_json"]!.GetValue<string>(),seal["fingerprint"]!.GetValue<string>());
            else
            {
                var snapshot = new JsonObject { ["version"]="advisor-observation-v1",["report_id"]=id,["status"]=reportStatus,["report"]=result?["report"]?.DeepClone(),["error_code"]=code };
                var snapshotJson = snapshot.ToJsonString();
                await sql.ExecuteAsync("INSERT INTO advisor_decision_snapshots(user_id,report_id,captured_at,snapshot_json,fingerprint) VALUES($1,$2,now(),$3::json,$4) ON CONFLICT(user_id,report_id) DO NOTHING",user,id,snapshotJson,Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(snapshotJson))).ToLowerInvariant());
            }
            await sql.ExecuteAsync("UPDATE advisor_schedule_runs SET status=$3,error_code=$4 WHERE user_id=$1 AND report_id=$2 AND status IN('claimed','queued','running')",user,id,reportStatus,code);
            await sql.ExecuteAsync("INSERT INTO advisor_notifications(user_id,id,kind,report_id,status,error_code,created_at,acknowledged) SELECT user_id,$3,kind,report_id,status,error_code,now(),false FROM market_advisor_reports WHERE user_id=$1 AND report_id=$2",user,id,Guid.NewGuid().ToString("N"));
        }
        if (kind == "agent.research")
        {
            var attempt = payload["public_attempt"]!.GetValue<int>();
            var publicStatus = success ? "succeeded" : status == "cancelled" ? "cancelled" : status == "interrupted" ? "interrupted" : "failed";
            await sql.ExecuteAsync("UPDATE agent_research_jobs SET status=$3,phase=$3,error_code=$4 WHERE user_id=$1 AND job_id=$2 AND attempt_no=$5 AND status IN('queued','running','cancel_requested')",user,id,publicStatus,error,attempt);
            await sql.ExecuteAsync("UPDATE agent_research_attempts SET execution_status=$4,report_json=$5::json,usage_json=$6::json,completed_at=now() WHERE user_id=$1 AND job_id=$2 AND attempt_no=$3 AND execution_status IN('queued','running','cancel_requested')",user,id,attempt,publicStatus,result?["report"]?.ToJsonString(),result?["usage"]?.ToJsonString());
        }
        await sql.ExecuteAsync("UPDATE model_call_costs SET status='interrupted',stage='interrupted',unknown_reason='lease_interrupted' WHERE user_id=$1 AND job_id=$2 AND status='reserved'",user,computeId);
    }
}
