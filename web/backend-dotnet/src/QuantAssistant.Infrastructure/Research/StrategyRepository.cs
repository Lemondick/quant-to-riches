using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Research;

/// <summary>E5：策略设置仓储（原 StrategyController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class StrategyRepository(IAccountDatabase database)
{
    public async Task<JsonObject> GetSettingsAsync(string userId, CancellationToken cancellation = default)
    {
        var rows = await database.QueryAsync("SELECT payload FROM user_documents WHERE user_id=$1 AND document_key='strategy-settings'", [userId], cancellation);
        return rows.FirstOrDefault()?["payload"]!.AsObject() ?? new() { ["parameters"] = new JsonObject(), ["updated_at"] = null };
    }

    public async Task SaveSettingsAsync(string userId, JsonObject result, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, sql => sql.ExecuteAsync(
            "INSERT INTO user_documents(user_id,document_key,revision,payload,updated_at) VALUES($1,'strategy-settings',1,$2::jsonb,now()) ON CONFLICT(user_id,document_key) DO UPDATE SET revision=user_documents.revision+1,payload=EXCLUDED.payload,updated_at=now()",
            userId, result.ToJsonString()), cancellation);

    public async Task SavePreviewSnapshotAsync(string userId, string snapshotId, string contentHash, string payloadJson,
        DateTime createdAt, DateTime expiresAt, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, sql => sql.ExecuteAsync(
            "INSERT INTO agent_preview_snapshots(user_id,snapshot_id,content_hash,payload_json,created_at,expires_at) VALUES($1,$2,$3,$4::json,$5,$6) ON CONFLICT(user_id,snapshot_id) DO NOTHING",
            userId, snapshotId, contentHash, payloadJson, createdAt, expiresAt), cancellation);
}
