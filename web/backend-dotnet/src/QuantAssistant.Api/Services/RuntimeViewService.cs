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
public sealed class RuntimeViewService(IAccountDatabase database, NativeOperationsMonitor monitor, ModelCostRepository costs)
{
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
