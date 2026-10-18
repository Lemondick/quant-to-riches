using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Caching.Memory;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Db;
using System.Collections.Concurrent;

namespace QuantAssistant.Api.Services;

public sealed record PublicReadResult(JsonNode? Data, string Cache);

public sealed class ComputeService(ComputeJobRepository jobs, AnalysisSnapshotRepository snapshots, IConfiguration configuration, IMemoryCache cache, PublicSnapshotRepository? shared = null)
{
    private readonly ConcurrentDictionary<string, Lazy<Task>> refreshes = new();
    private static readonly JsonSerializerOptions InputOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    public async Task QueuePublicRefresh(string userId, string kind, object input, CancellationToken cancellation, bool force = false)
    {
        if (!ComputeJobRepository.IndependentPublicKinds.Contains(kind)) throw new ArgumentException("公开资料类型无效");
        if (configuration["QUANT_WORKER_SECRET"] is not { Length: >= 32 }) return;
        var payload = JsonSerializer.Serialize(input, InputOptions);
        var key = CacheKey(userId, kind, payload)! + (force ? ":refresh" : "");
        var pending = refreshes.GetOrAdd(key, requestKey => new Lazy<Task>(async () =>
        {
            try { await jobs.EnqueueAsync(userId, kind, payload, DateTime.UtcNow.AddSeconds(60), reuseCompleted: !force); }
            catch (JobQuotaExceeded) { }
            finally { refreshes.TryRemove(key, out _); }
        }));
        await pending.Value.WaitAsync(cancellation);
    }

    private static string? CacheKey(string userId, string kind, string payload, bool accountSnapshot = false) =>
        !accountSnapshot && ComputeJobRepository.PublicCacheSeconds(kind) > 0
            ? $"compute:{userId}:{kind}:{Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(payload)))}" : null;

    private void Remember(string? key, string kind, JsonNode? data, DateTime? completedAt)
    {
        if (key is null || data is null || completedAt is null || data["stale"]?.GetValue<bool>() == true) return;
        var expiresAt = completedAt.Value.AddSeconds(ComputeJobRepository.PublicCacheSeconds(kind));
        if (expiresAt > DateTime.UtcNow) cache.Set(key, data.DeepClone(), new DateTimeOffset(expiresAt));
    }

    public async Task<PublicReadResult> ReadPublic(string userId, string kind, object input, CancellationToken cancellation, bool refresh = false)
    {
        if (kind is not ("fund.detail" or "market.snapshot" or "market.history")) throw new ArgumentException("公开资料类型无效");
        cancellation.ThrowIfCancellationRequested();
        if (refresh && shared is not null)
        {
            var payload = JsonSerializer.Serialize(input, InputOptions);
            cache.Remove(CacheKey(userId, kind, payload)!);
            await QueuePublicRefresh(userId, kind, input, cancellation, force: true);
            if (await shared.ReadAsync(kind, payload, cancellation) is { } previous)
            {
                var data = JsonNode.Parse(previous.ResultJson)!;
                data["stale"] = true;
                return new(data, "refreshing");
            }
        }
        if (!refresh)
        {
            var payload = JsonSerializer.SerializeToNode(input, InputOptions)!.ToJsonString();
            var key = CacheKey(userId, kind, payload)!;
            if (cache.TryGetValue(key, out JsonNode? memory)) return new(memory!.DeepClone(), "hit");
            var cached = (shared is null ? null : await shared.ReadAsync(kind, payload, cancellation)) ?? await jobs.ReadPublicCacheAsync(userId, kind, payload, cancellation);
            if (cached is not null)
            {
                var data = JsonNode.Parse(cached.ResultJson)!;
                if (!cached.Fresh)
                {
                    data["stale"] = true;
                    await QueuePublicRefresh(userId, kind, input, cancellation, force: true);
                }
                else Remember(key, kind, data, cached.CompletedAt);
                return new(data, cached.Fresh ? "hit" : "stale");
            }
        }
        if (shared is not null)
        {
            await QueuePublicRefresh(userId, kind, input, cancellation);
            // Display requests never wait for a remote crawler, including the
            // first lookup. A missing observation remains explicitly unavailable.
            throw new ApplicationFailure("public_snapshot_unavailable", 503, "公开资料正在准备，请稍后刷新。");
        }
        return new(await Run(userId, kind, input, cancellation, reuseCompleted: !refresh), refresh ? "refresh" : "miss");
    }

    public async Task<JsonNode?> Run(string userId, string kind, object input, CancellationToken cancellation, bool accountSnapshot = false, int seconds = 60, bool reuseCompleted = true)
    {
        cancellation.ThrowIfCancellationRequested();
        var payload = JsonSerializer.SerializeToNode(input, InputOptions)!.AsObject();
        var key = CacheKey(userId, kind, payload.ToJsonString(), accountSnapshot);
        if (key is not null)
        {
            if (reuseCompleted && cache.TryGetValue(key, out JsonNode? memory)) return memory!.DeepClone();
            if (!reuseCompleted) cache.Remove(key);
            else if (shared is not null && await shared.ReadAsync(kind, payload.ToJsonString(), cancellation) is { Fresh: true } saved)
            {
                var data = JsonNode.Parse(saved.ResultJson);
                Remember(key, kind, data, saved.CompletedAt);
                return data;
            }
        }
        if (configuration["QUANT_WORKER_SECRET"] is not { Length: >= 32 }) throw new ApplicationFailure("worker_unavailable", 503, "计算进程尚未配置。");
        if (accountSnapshot) payload["snapshot"] = await snapshots.CreateAsync(userId, cancellation);
        var deadline = DateTime.UtcNow.AddSeconds(seconds);
        var id = await jobs.EnqueueAsync(userId, kind, payload.ToJsonString(), deadline, reuseCompleted, cancellation);
        using var signal = ComputeJobNotifications.Local.Subscribe(userId, id);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        timeout.CancelAfter(TimeSpan.FromSeconds(seconds));
        using var timing = PerformanceTrace.Measure("gateway_wait");
        try
        {
          while (DateTime.UtcNow < deadline)
          {
            var result = await jobs.ReadAsync(userId, id, timeout.Token);
            if (result.Status == "completed")
            {
                var data = JsonNode.Parse(result.ResultJson!);
                PerformanceTrace.Current?.Record("queue", result.QueueMilliseconds ?? 0);
                if (result.Timings is JsonObject timings)
                    foreach (var pair in timings)
                        if (pair.Value is JsonValue value && value.TryGetValue<double>(out var duration)) PerformanceTrace.Current?.Record(pair.Key, duration);
                Remember(key, kind, data, result.CompletedAt);
                return data;
            }
            if (result.Status is "failed" or "cancelled" or "interrupted")
                throw new ApplicationFailure(result.ErrorCode == "invalid_request" ? "invalid_request" : "worker_unavailable", result.ErrorCode == "invalid_request" ? 422 : 503,
                    result.ErrorCode == "invalid_request" ? "研究参数或证据无效。" : "计算未完成，请检查计算进程后重试。");
            await signal.WaitAsync(TimeSpan.FromSeconds(5), timeout.Token);
          }
        }
        catch (OperationCanceledException) when (!cancellation.IsCancellationRequested) { }
        throw new ApplicationFailure("worker_unavailable", 503, "计算超时，请检查计算进程后重试。");
    }
}
