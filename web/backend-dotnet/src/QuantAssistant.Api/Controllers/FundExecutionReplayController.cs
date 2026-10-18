using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/research/funds/execution-replays")]
public sealed partial class FundExecutionReplayController(AccountDatabase database, ComputeService compute) : ControllerBase
{
    private const string Prefix = "fund-execution-replay:";
    private string UserId => User.FindFirst("sub")!.Value;
    public sealed record ReplayBody(JsonObject Bundle, string? RequestId = null);
    [GeneratedRegex("^[a-f0-9]{32}$")] private static partial Regex Identifier();

    private static JsonNode? Canonical(JsonNode? value) => value switch
    {
        JsonObject obj => new JsonObject(obj.OrderBy(p => p.Key, StringComparer.Ordinal).Select(p => new KeyValuePair<string, JsonNode?>(p.Key, Canonical(p.Value)))),
        JsonArray array => new JsonArray(array.Select(Canonical).ToArray()),
        JsonValue number when number.TryGetValue<decimal>(out var amount) => JsonNode.Parse(amount.ToString("G29", System.Globalization.CultureInfo.InvariantCulture)),
        _ => value?.DeepClone()
    };
    private static string Fingerprint(JsonObject value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Canonical(value)!.ToJsonString()))).ToLowerInvariant();
    private static ApplicationFailure IntegrityFailure() => new("replay_integrity_failed", 409, "回放归档校验失败，已隐藏结果。可从归档列表删除损坏记录后重新回放。");
    private static JsonObject Verified(string raw, string id)
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
    private JsonObject? Existing(AccountSql sql, string id, JsonObject bundle)
    {
        if (sql.Scalar("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key=$2", UserId, Prefix + id) is not string raw) return null;
        var saved = Verified(raw, id);
        if (Fingerprint(saved["bundle"]!.AsObject()) != Fingerprint(bundle))
            throw new ApplicationFailure("replay_request_conflict", 409, "此请求编号已经归档另一份证据，请重新选择文件后提交。");
        return saved;
    }
    private void CheckQuota(AccountSql sql)
    {
        if ((long)sql.Scalar("SELECT count(*) FROM user_documents WHERE user_id=$1 AND document_key LIKE 'fund-execution-replay:%'", UserId)! >= 20)
            throw new ApplicationFailure("replay_archive_quota", 409, "已保留20份基金执行回放，请先导出并删除不再需要的归档。");
    }
    [HttpPost, RequestSizeLimit(2 * 1024 * 1024)]
    public async Task<IActionResult> Create(ReplayBody body, CancellationToken cancellation)
    {
        if (body.Bundle is null || Encoding.UTF8.GetByteCount(body.Bundle.ToJsonString()) > 2 * 1024 * 1024) throw new ArgumentException("证据包过大或为空");
        if (body.Bundle["symbol"] is not JsonValue symbol || !symbol.TryGetValue<string>(out var symbolText)) throw new ArgumentException("请填写字符串基金代码");
        FundFeeRepository.Symbol(symbolText);
        var id = body.RequestId ?? Guid.NewGuid().ToString("N");
        if (!Identifier().IsMatch(id)) throw new ArgumentException("回放请求编号无效");
        var existing = database.Read(sql => { var saved = Existing(sql, id, body.Bundle); if (saved is null) CheckQuota(sql); return saved; });
        if (existing is not null) return Ok(existing);
        var result = (await compute.Run(UserId, "fund.execution_replay", new { bundle = body.Bundle }, cancellation))!.AsObject();
        result["research_only"] = true;
        result["qualifies_strategy"] = false;
        result["qualifies_paper_days"] = false;
        result["ready_for_trading"] = false;
        var record = new JsonObject { ["id"] = id, ["created_at"] = DateTimeOffset.UtcNow.ToString("O"), ["bundle"] = body.Bundle.DeepClone(), ["result"] = result };
        record["fingerprint"] = Fingerprint(record);
        var committed = database.Write(UserId, sql =>
        {
            var saved = Existing(sql, id, body.Bundle);
            if (saved is not null) return saved;
            CheckQuota(sql);
            sql.Execute("INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,$2,1,$3::jsonb)", UserId, Prefix + id, record.ToJsonString());
            sql.Execute("INSERT INTO audit_log(user_id,changed_at,entity_type,entity_id,after_json) VALUES($1,now(),'fund_execution_replay',$2,$3::json)", UserId, id, new JsonObject { ["fingerprint"] = record["fingerprint"]!.DeepClone() }.ToJsonString());
            return record;
        });
        return ReferenceEquals(committed, record) ? Created("/api/v1/research/funds/execution-replays/" + id, record) : Ok(committed);
    }
    [HttpGet]
    public IActionResult List() => Ok(database.Read(sql => new JsonObject
    {
        ["items"] = new JsonArray(sql.Query("SELECT document_key,updated_at,payload::text AS raw FROM user_documents WHERE user_id=$1 AND document_key LIKE 'fund-execution-replay:%' ORDER BY updated_at DESC,document_key DESC LIMIT 20", UserId).OfType<JsonObject>().Select(row =>
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
        }).ToArray())
    }));
    private JsonObject Read(string id)
    {
        if (!Identifier().IsMatch(id)) throw new ApplicationFailure("replay_not_found", 404, "未找到执行回放。");
        return database.Read(sql => sql.Scalar("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key=$2", UserId, Prefix + id) is string raw ? Verified(raw, id) : throw new ApplicationFailure("replay_not_found", 404, "未找到执行回放。"));
    }
    [HttpGet("{id}")] public IActionResult Get(string id) => Ok(Read(id));
    [HttpGet("{id}/export")] public IActionResult Export(string id) => File(Encoding.UTF8.GetBytes(Read(id).ToJsonString()), "application/json; charset=utf-8", "fund-execution-replay-" + id + ".json");
    [HttpDelete("{id}")]
    public IActionResult Delete(string id)
    {
        if (!Identifier().IsMatch(id)) throw new ApplicationFailure("replay_not_found", 404, "未找到执行回放。");
        var changed = database.Write(UserId, sql =>
        {
            var count = sql.Execute("DELETE FROM user_documents WHERE user_id=$1 AND document_key=$2", UserId, Prefix + id);
            if (count > 0) sql.Execute("INSERT INTO audit_log(user_id,changed_at,entity_type,entity_id,after_json) VALUES($1,now(),'fund_execution_replay_deleted',$2,'{}'::json)", UserId, id);
            return count;
        });
        return changed == 0 ? NotFound(new {error_code="replay_not_found", message="未找到执行回放。"}) : Ok(new {status="deleted"});
    }
}
