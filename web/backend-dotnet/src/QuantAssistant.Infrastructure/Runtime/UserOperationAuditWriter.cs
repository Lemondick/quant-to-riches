using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Runtime;

/// <summary>
/// F6：用户业务写操作统一审计（操作者/时间/对象/前后值摘要 → user_operation_audit 表，
/// 0026 迁移创建）。前后值只存摘要（截断防膨胀）。
/// 审计写入为 best-effort：失败只记日志，不影响主业务写（主写已提交时不能再回滚）。
/// </summary>
public sealed class UserOperationAuditWriter(AccountDatabase database, ILogger<UserOperationAuditWriter> logger)
{
    private const int MaxJsonChars = 4000;

    public async Task RecordAsync(string actorId, string action, string targetType, string? targetId,
        JsonNode? before, JsonNode? after, CancellationToken cancellation = default)
    {
        try
        {
            await database.WriteNonLedgerAsync(actorId, async sql =>
            {
                await sql.ExecuteAsync(
                    "INSERT INTO user_operation_audit(actor_id,action,target_type,target_id,before_json,after_json) VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb)",
                    actorId, action, targetType, targetId, Summarize(before), Summarize(after));
                return 0;
            }, cancellation);
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            logger.LogWarning("用户操作审计写入失败（{Action}/{TargetType}）：{ErrorType}", action, targetType, error.GetType().Name);
        }
    }

    /// <summary>同步版本，供同步控制器动作使用。</summary>
    public void Record(string actorId, string action, string targetType, string? targetId, JsonNode? before, JsonNode? after) =>
        RecordAsync(actorId, action, targetType, targetId, before, after).GetAwaiter().GetResult();

    private static string? Summarize(JsonNode? node)
    {
        if (node is null) return null;
        var json = node.ToJsonString();
        return json.Length > MaxJsonChars ? json[..MaxJsonChars] : json;
    }
}
