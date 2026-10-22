using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch3 流 A：模型调用去重缓存。
/// 键包含账户身份、模型版本和 canonical_json(prompt)，避免私人结果跨账户复用；
/// TTL 走 QUANT_MODEL_CACHE_TTL_MIN（默认 60 分钟），开关 QUANT_MODEL_CACHE_ENABLED（默认 true）；
/// 命中返回 cached:true（由调用方在响应中标记），DB 侧审计记 model_call_cache.hits+1。
/// model_call_cache 表由流 D 建（0040：prompt_hash PK, model_version, result_json, hits, created_at, expires_at）；
/// 表不存在时降级为内存缓存并记 warning，不抛错（应用重启内存缓存清零）。
/// 局限（commit 注明）：API 侧只知道 worker op（如 ask.answer），不知道 worker 选用的具体模型，
/// 因此 model_version 取 op 作用域版本（如 "ask.answer/v1"）；worker 更换模型时须升级版本，
/// 否则可能命中旧模型结果。worker 内部（advisor.report/agent.research）的模型调用发生在
/// Python worker 进程，C# 后端无法拦截；B 流如需复用，键格式与表结构按本契约实现即可。
/// </summary>
public sealed class ModelCallCacheService(AccountDatabase database, QuantOptions options, ILogger<ModelCallCacheService> logger)
{
    private sealed record MemoryEntry(string ResultJson, DateTimeOffset ExpiresAt);

    private readonly ConcurrentDictionary<string, MemoryEntry> _memory = new();
    private DateTime _lastProbe = DateTime.MinValue;
    private bool _tableExists;
    private bool _warnedMissing;

    /// <summary>canonical_json：递归按 key 排序对象属性（Ordinal），数组保序，值原样。
    /// Python 侧等价写法：json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)。</summary>
    public static string CanonicalJson(JsonNode? node)
    {
        if (node is JsonObject obj)
        {
            var sorted = new JsonObject();
            foreach (var property in obj.OrderBy(p => p.Key, StringComparer.Ordinal))
                sorted[property.Key] = property.Value is null ? null : JsonNode.Parse(CanonicalJson(property.Value));
            return sorted.ToJsonString();
        }
        if (node is JsonArray array)
        {
            var rebuilt = new JsonArray();
            foreach (var item in array) rebuilt.Add(item is null ? null : JsonNode.Parse(CanonicalJson(item)));
            return rebuilt.ToJsonString();
        }
        return node?.ToJsonString() ?? "null";
    }

    public static string CacheKey(string userId, string modelVersion, JsonNode prompt) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(CanonicalJson(new JsonObject
        {
            ["user_id"] = userId, ["model_version"] = modelVersion, ["prompt"] = prompt.DeepClone(),
        })))).ToLowerInvariant();

    private async Task RequireActiveUserAsync(string userId, CancellationToken cancellation)
    {
        var rows = await database.QueryAsync("SELECT 1 FROM users WHERE id=$1 AND status='active'", [userId], cancellation);
        if (rows.Count == 0) throw new AuthFailure("authentication_required");
    }

    private (bool Hit, JsonNode? Result) ReadMemory(string key)
    {
        if (_memory.TryGetValue(key, out var entry) && entry.ExpiresAt > DateTimeOffset.UtcNow)
            return (true, JsonNode.Parse(entry.ResultJson));
        _memory.TryRemove(key, out _);
        return (false, null);
    }

    private bool TableExists()
    {
        if (DateTime.UtcNow - _lastProbe < TimeSpan.FromMinutes(5)) return _tableExists;
        _lastProbe = DateTime.UtcNow;
        try
        {
            _tableExists = Convert.ToInt64(database.Read(sql => sql.Scalar(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='model_call_cache'"))) > 0;
        }
        catch (Exception)
        {
            _tableExists = false;
        }
        if (!_tableExists && !_warnedMissing)
        {
            _warnedMissing = true;
            logger.LogWarning("model_call_cache 表不存在（流 D 0040 未应用），模型去重缓存降级为内存实现。");
        }
        return _tableExists;
    }

    /// <summary>命中返回 (true, 结果)；调用方在响应中标记 cached:true。未命中/未启用返回 (false, null)。</summary>
    public async Task<(bool Hit, JsonNode? Result)> TryGetAsync(string userId, string modelVersion, JsonNode prompt,
        CancellationToken cancellation = default)
    {
        if (!options.ModelCacheEnabled) return (false, null);
        await RequireActiveUserAsync(userId, cancellation);
        var key = CacheKey(userId, modelVersion, prompt);
        var memory = ReadMemory(key);
        if (memory.Hit || !TableExists()) return memory;
        JsonArray rows;
        try
        {
            rows = await database.QueryAsync(
                "SELECT result_json::text AS result_json FROM model_call_cache WHERE prompt_hash=$1 AND expires_at > now()",
                [key], cancellation);
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            logger.LogWarning("model_call_cache 读取失败，降级为未命中：{ErrorType}", error.GetType().Name);
            return (false, null);
        }
        var json = rows.FirstOrDefault()?["result_json"]?.GetValue<string>();
        if (json is null) return (false, null);
        // 审计：hits+1（best-effort，不影响返回）。
        try
        {
            await database.WriteNonLedgerAsync(userId,
                sql => sql.ExecuteAsync("UPDATE model_call_cache SET hits=hits+1 WHERE prompt_hash=$1", key), cancellation);
        }
        catch (Exception) { /* 审计失败不影响命中返回 */ }
        return (true, JsonNode.Parse(json));
    }

    /// <summary>写入/刷新缓存（TTL 到期覆盖）。失败降级为内存写入，不抛错。</summary>
    public async Task SetAsync(string userId, string modelVersion, JsonNode prompt, JsonNode result,
        CancellationToken cancellation = default)
    {
        if (!options.ModelCacheEnabled) return;
        await RequireActiveUserAsync(userId, cancellation);
        var key = CacheKey(userId, modelVersion, prompt);
        var ttlMinutes = Math.Max(1, options.ModelCacheTtlMinutes);
        var json = result.ToJsonString();
        if (!TableExists())
        {
            _memory[key] = new MemoryEntry(json, DateTimeOffset.UtcNow.AddMinutes(ttlMinutes));
            return;
        }
        try
        {
            await database.WriteNonLedgerAsync(userId, sql => sql.ExecuteAsync("""
                INSERT INTO model_call_cache(prompt_hash,model_version,result_json,hits,created_at,expires_at)
                VALUES($1,$2,$3::json,0,now(),now()+($4::int * interval '1 minute'))
                ON CONFLICT(prompt_hash) DO UPDATE SET model_version=EXCLUDED.model_version,
                    result_json=EXCLUDED.result_json,hits=0,created_at=now(),expires_at=EXCLUDED.expires_at
                """, key, modelVersion, json, ttlMinutes), cancellation);
            _memory.TryRemove(key, out _);
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            logger.LogWarning("model_call_cache 写入失败，降级为内存缓存：{ErrorType}", error.GetType().Name);
            _memory[key] = new MemoryEntry(json, DateTimeOffset.UtcNow.AddMinutes(ttlMinutes));
        }
    }
}
