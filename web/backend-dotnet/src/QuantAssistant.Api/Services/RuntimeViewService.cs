using System.Security.Claims;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Services;

/// <summary>
/// E5：运行时视图服务（原 RuntimeController 内嵌 SQL/监视器调用迁移至此，语义一致）。
/// 放在 Api 层而非 Infrastructure，因为 NativeOperationsMonitor 是 Api 层类型。
/// </summary>
public sealed class RuntimeViewService(IAccountDatabase database, NativeOperationsMonitor monitor, ModelCostRepository costs,
    NativeBackupEvidence backups, NativeSiteLock siteLock, NativeScheduler scheduler)
{
    public async Task<JsonObject> AdminStatusAsync(ClaimsPrincipal user, CancellationToken cancellation = default)
    {
        if (user.FindFirst("role")?.Value != "admin") throw new AuthFailure("admin_required");
        var archives = backups.Read();
        return await database.ReadAsync(async sql =>
        {
            var workers = await sql.QueryAsync("SELECT worker_id,capabilities,last_seen_at,stopping FROM worker_heartbeats ORDER BY worker_id");
            var operations = monitor.Read(sql, archives);
            var alive = operations["workers"]!["status"]!.GetValue<string>() == "healthy";
            var accounts = await sql.QueryAsync("SELECT id AS user_id,username,status FROM users ORDER BY created_at");
            foreach (var account in accounts.OfType<JsonObject>())
            {
                var id = account["user_id"]!.GetValue<string>();
                account["data_status"] = "ready";
                account["tasks"] = await sql.QueryAsync("SELECT 'advisor' AS source,report_id AS id,status,error_code,updated_at FROM market_advisor_reports WHERE user_id=$1 UNION ALL SELECT 'research',job_id,status,error_code,created_at FROM agent_research_jobs WHERE user_id=$1 ORDER BY updated_at DESC LIMIT 20", id);
                account["failure_count"] = Convert.ToInt64(await sql.ScalarAsync("SELECT (SELECT count(*) FROM market_advisor_reports WHERE user_id=$1 AND status='failed')+(SELECT count(*) FROM agent_research_jobs WHERE user_id=$1 AND status IN('failed','timed_out','interrupted'))", id));
                account["workers"] = new JsonObject { ["advisor"] = alive, ["research"] = alive };
                account["costs"] = costs.SummaryFor(sql, id);
            }
            return new JsonObject
            {
                ["accounts"] = accounts, ["workers"] = workers,
                ["global_model_gate"] = new JsonObject { ["enabled"] = true, ["limit"] = 3, ["active"] = Convert.ToInt64(await sql.ScalarAsync("SELECT count(*) FROM model_call_costs WHERE status='reserved'")), ["stopping"] = false },
                ["operations"] = operations, ["site_backups"] = archives, ["site_lock_held"] = siteLock.Held,
                ["scheduler_running"] = scheduler.Running, ["business_ready"] = alive, ["automatic_trading"] = false,
            };
        }, cancellation);
    }

    public async Task<JsonObject> StatusAsync(ClaimsPrincipal user, CancellationToken cancellation = default)
    {
        var id = user.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
        var account = await database.ReadAsync(async sql =>
        {
            var users = await sql.QueryAsync("SELECT id,role,display_name,timezone,status,created_at,last_login_at,last_active_at FROM users WHERE id=$1", id);
            var node = users[0]!.AsObject(); node.Remove("id");
            node["costs"] = costs.SummaryFor(sql, id);
            var archives = (await sql.QueryAsync("SELECT id,finished_at,status FROM site_archives ORDER BY finished_at DESC LIMIT 1")).FirstOrDefault()?.AsObject();
            node["archive"] = archives is null ? new JsonObject { ["status"] = "none" } : new JsonObject { ["status"] = archives["status"]!.DeepClone(), ["finished_at"] = archives["finished_at"]!.DeepClone() };
            return node;
        }, cancellation);
        var runtime = await database.ReadAsync(sql =>
            Task.FromResult(monitor.Read(sql, sql.Query("SELECT id,finished_at,status,verified,created_at,modified_at FROM site_archives ORDER BY finished_at DESC LIMIT 20"))), cancellation);
        account["runtime"] = runtime;
        return account;
    }

    public async Task<JsonObject> WorkerSnapshotAsync(string userId, CancellationToken cancellation = default) =>
        await database.ReadAsync(sql => Task.FromResult(monitor.Workers(sql)), cancellation);
}
