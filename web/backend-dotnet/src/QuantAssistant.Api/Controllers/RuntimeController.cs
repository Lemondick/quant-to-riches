using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1/admin")]
public sealed class RuntimeController(AccountDatabase database,NativeBackupEvidence backups,NativeSiteLock siteLock,NativeScheduler scheduler,NativeOperationsMonitor monitor):ControllerBase
{
    [HttpGet("runtime")]
    public IActionResult Runtime()
    {
      var archives = backups.Read();
      return Ok(database.WriteNonLedger(User.FindFirst("sub")!.Value,sql=>
    {
        var workers=sql.Query("SELECT worker_id,capabilities,last_seen_at,stopping FROM worker_heartbeats ORDER BY worker_id");
        var operations=monitor.Read(sql, archives);
        var alive=operations["workers"]!["status"]!.GetValue<string>()=="healthy";
        var accounts=sql.Query("SELECT id AS user_id,username,status FROM users ORDER BY created_at");
        foreach(var account in accounts.OfType<JsonObject>())
        {
            var id=account["user_id"]!.GetValue<string>();account["data_status"]="ready";
            account["tasks"]=sql.Query("SELECT 'advisor' AS source,report_id AS id,status,error_code,updated_at FROM market_advisor_reports WHERE user_id=$1 UNION ALL SELECT 'research',job_id,status,error_code,created_at FROM agent_research_jobs WHERE user_id=$1 ORDER BY updated_at DESC LIMIT 20",id);
            account["failure_count"]=Convert.ToInt64(sql.Scalar("SELECT (SELECT count(*) FROM market_advisor_reports WHERE user_id=$1 AND status='failed')+(SELECT count(*) FROM agent_research_jobs WHERE user_id=$1 AND status IN('failed','timed_out','interrupted'))",id));
            account["workers"]=new JsonObject{["advisor"]=alive,["research"]=alive};
            account["costs"]=ModelCostRepository.SummaryFor(sql,id);
            sql.Execute("INSERT INTO admin_audit(actor_id,action,target_id,detail,created_at) VALUES($1,'runtime_view',$2,'{\"category\":\"runtime_tasks\"}',now())",User.FindFirst("sub")!.Value,id);
        }
        return new JsonObject{["accounts"]=accounts,["workers"]=workers,["global_model_gate"]=new JsonObject{["enabled"]=true,["limit"]=3,["active"]=Convert.ToInt64(sql.Scalar("SELECT count(*) FROM model_call_costs WHERE status='reserved'")),["stopping"]=false},
            ["operations"]=operations,["site_backups"]=archives,["site_lock_held"]=siteLock.Held,["scheduler_running"]=scheduler.Running,["business_ready"]=alive,["automatic_trading"]=false};
    }));
    }
}
