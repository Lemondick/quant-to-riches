using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Infrastructure.Research;

/// <summary>E5：基金执行回放仓储（原 FundExecutionReplayController 内嵌 SQL 迁移至此，语义一致）。</summary>
public sealed class FundExecutionReplayRepository(IAccountDatabase database)
{
    private const string Prefix = "fund-execution-replay:";

    public async Task<JsonObject?> FindExistingAsync(string userId, string id, JsonObject bundle, bool checkQuota, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
        {
            var saved = Existing(sql, userId, id, bundle);
            if (saved is null && checkQuota) CheckQuota(sql, userId);
            return saved;
        }, cancellation);

    public async Task<JsonObject> CommitAsync(string userId, string id, JsonObject bundle, JsonObject record, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var saved = Existing(sql, userId, id, bundle);
            if (saved is not null) return saved;
            CheckQuota(sql, userId);
            await sql.ExecuteAsync("INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,$2,1,$3::jsonb)", userId, Prefix + id, record.ToJsonString());
            await sql.ExecuteAsync("INSERT INTO audit_log(user_id,changed_at,entity_type,entity_id,after_json) VALUES($1,now(),'fund_execution_replay',$2,$3::json)", userId, id, new JsonObject { ["fingerprint"] = record["fingerprint"]!.DeepClone() }.ToJsonString());
            return record;
        }, cancellation);

    public async Task<JsonObject> ListAsync(string userId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql => new JsonObject
        {
            ["items"] = new JsonArray((await sql.QueryAsync("SELECT document_key,updated_at,payload::text AS raw FROM user_documents WHERE user_id=$1 AND document_key LIKE 'fund-execution-replay:%' ORDER BY updated_at DESC,document_key DESC LIMIT 20", userId)).OfType<JsonObject>().Select(row =>
            {
                var id = row["document_key"]!.GetValue<string>()[Prefix.Length..];
                try
                {
                    var record = Verified(row["raw"]!.GetValue<string>(), id);
                    return (JsonNode?)new JsonObject { ["id"] = id, ["created_at"] = record["created_at"]!.DeepClone(), ["symbol"] = record["bundle"]!["symbol"]!.DeepClone(), ["status"] = record["result"]!["status"]!.DeepClone(), ["integrity_valid"] = true };
                }
                catch (ApplicationFailure error) when (error.Code == "replay_integrity_failed")
                {
                    return new JsonObject { ["id"] = id, ["created_at"] = row["updated_at"]!.DeepClone(), ["symbol"] = null, ["status"] = "corrupted", ["integrity_valid"] = false };
                }
            }).ToArray()),
        }, cancellation);

    public async Task<JsonObject> ReadAsync(string userId, string id, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
            await sql.ScalarAsync("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key=$2", userId, Prefix + id) is string raw
                ? Verified(raw, id)
                : throw new ApplicationFailure("replay_not_found", 404, "未找到执行回放。"), cancellation);

    public async Task<int> DeleteAsync(string userId, string id, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var count = await sql.ExecuteAsync("DELETE FROM user_documents WHERE user_id=$1 AND document_key=$2", userId, Prefix + id);
            if (count > 0) await sql.ExecuteAsync("INSERT INTO audit_log(user_id,changed_at,entity_type,entity_id,after_json) VALUES($1,now(),'fund_execution_replay_deleted',$2,'{}'::json)", userId, id);
            return count;
        }, cancellation);

    private static JsonObject? Existing(IAccountSql sql, string userId, string id, JsonObject bundle)
    {
        if (sql.Scalar("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key=$2", userId, Prefix + id) is not string raw) return null;
        var saved = Verified(raw, id);
        if (Fingerprint(saved["bundle"]!.AsObject()) != Fingerprint(bundle))
            throw new ApplicationFailure("replay_request_conflict", 409, "此请求编号已经归档另一份证据，请重新选择文件后提交。");
        return saved;
    }

    private static void CheckQuota(IAccountSql sql, string userId)
    {
        if ((long)sql.Scalar("SELECT count(*) FROM user_documents WHERE user_id=$1 AND document_key LIKE 'fund-execution-replay:%'", userId)! >= 20)
            throw new ApplicationFailure("replay_archive_quota", 409, "已保留20份基金执行回放，请先导出并删除不再需要的归档。");
    }

    public static ApplicationFailure IntegrityFailure() => new("replay_integrity_failed", 409, "回放归档校验失败，已隐藏结果。可从归档列表删除损坏记录后重新回放。");

    public static JsonObject Verified(string raw, string id)
    {
        try
        {
            var value = JsonNode.Parse(raw)?.AsObject() ?? throw IntegrityFailure();
            var expected = value["fingerprint"]?.GetValue<string>();
            if (value["id"]?.GetValue<string>() != id || !DateTimeOffset.TryParse(value["created_at"]?.GetValue<string>(), out _) ||
                value["bundle"] is not JsonObject bundle || bundle["symbol"] is not JsonValue symbol || !symbol.TryGetValue<string>(out _) ||
                value["result"] is not JsonObject result || result["status"]?.GetValue<string>() is not ("completed" or "blocked") ||
                result["fills"] is not JsonArray || result["blockers"] is not JsonArray || result["rejections"] is not JsonArray)
                throw IntegrityFailure();
            value.Remove("fingerprint");
            if (Fingerprint(value) != expected) throw IntegrityFailure();
            value["fingerprint"] = expected;
            return value;
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException or FormatException) { throw IntegrityFailure(); }
    }

    public static string Fingerprint(JsonObject value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Canonical(value)!.ToJsonString()))).ToLowerInvariant();

    private static JsonNode? Canonical(JsonNode? value) => value switch
    {
        JsonObject obj => new JsonObject(obj.OrderBy(p => p.Key, StringComparer.Ordinal).Select(p => new KeyValuePair<string, JsonNode?>(p.Key, Canonical(p.Value)))),
        JsonArray array => new JsonArray(array.Select(Canonical).ToArray()),
        JsonValue number when number.TryGetValue<decimal>(out var amount) => JsonNode.Parse(amount.ToString("G29", System.Globalization.CultureInfo.InvariantCulture)),
        _ => value?.DeepClone(),
    };
}
