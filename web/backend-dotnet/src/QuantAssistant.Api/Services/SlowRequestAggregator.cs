using System.Collections.Concurrent;
using System.Text.Json.Nodes;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch3 流 A：慢查询聚合。
/// 在 PerformanceMiddleware 的 >500ms 慢日志基础上，按小时桶聚合（endpoint + 次数 + p95）。
/// 纯内存实现（单机部署）：应用重启清零；每桶样本上限 10000（防内存膨胀）；只保留近 2 小时桶。
/// endpoint 取路由模板（如 /api/v1/risk/metrics），避免路径参数导致基数爆炸。
/// </summary>
public sealed class SlowRequestAggregator
{
    private sealed class Bucket
    {
        public readonly List<double> Samples = new();
        public readonly Lock Sync = new();
    }

    private const int MaxSamplesPerBucket = 10000;
    private readonly ConcurrentDictionary<string, Bucket> _buckets = new();

    public static string HourKey(DateTime utc) => utc.ToString("yyyyMMddHH");

    public static string NormalizeEndpoint(string? endpoint)
    {
        var value = (endpoint ?? "").Trim();
        if (value.Length == 0) value = "(unknown)";
        return value.Length > 200 ? value[..200] : value;
    }

    /// <summary>记录一次慢请求（elapsedMs > 500，由 PerformanceMiddleware 调用）。</summary>
    public void Record(string endpoint, double elapsedMs)
    {
        var key = HourKey(DateTime.UtcNow) + "|" + NormalizeEndpoint(endpoint);
        var bucket = _buckets.GetOrAdd(key, _ => new Bucket());
        lock (bucket.Sync)
        {
            if (bucket.Samples.Count < MaxSamplesPerBucket) bucket.Samples.Add(elapsedMs);
        }
    }

    public sealed record EndpointStats(string Endpoint, int Count, double P95Ms);

    /// <summary>指定小时桶的聚合（endpoint + 次数 + p95），按次数降序。</summary>
    public IReadOnlyList<EndpointStats> SnapshotHour(string hourKey)
    {
        var prefix = hourKey + "|";
        var result = new List<EndpointStats>();
        foreach (var pair in _buckets)
        {
            if (!pair.Key.StartsWith(prefix, StringComparison.Ordinal)) continue;
            double[] samples;
            lock (pair.Value.Sync) samples = pair.Value.Samples.ToArray();
            if (samples.Length == 0) continue;
            Array.Sort(samples);
            var p95 = samples[Math.Min(samples.Length - 1, (int)Math.Ceiling(samples.Length * 0.95) - 1)];
            result.Add(new EndpointStats(pair.Key[prefix.Length..], samples.Length, p95));
        }
        return result.OrderByDescending(s => s.Count).ToList();
    }

    /// <summary>当前小时桶聚合（供 NativeOperationsMonitor 展示）。</summary>
    public IReadOnlyList<EndpointStats> SnapshotCurrentHour() => SnapshotHour(HourKey(DateTime.UtcNow));

    /// <summary>清理早于 keepHourKey 的桶（keepHourKey 本身保留）。</summary>
    public void Prune(string keepHourKey)
    {
        foreach (var pair in _buckets)
        {
            var sep = pair.Key.IndexOf('|');
            if (sep > 0 && string.CompareOrdinal(pair.Key, 0, keepHourKey, 0, keepHourKey.Length) < 0)
                _buckets.TryRemove(pair.Key, out _);
        }
    }

    /// <summary>供 NativeOperationsMonitor 输出的 JSON（含阈值与是否超限）。</summary>
    public JsonArray ToJson(IReadOnlyList<EndpointStats> stats, int thresholdCount, int thresholdP95Ms)
    {
        var array = new JsonArray();
        foreach (var s in stats)
            array.Add(new JsonObject
            {
                ["endpoint"] = s.Endpoint, ["count"] = s.Count, ["p95_ms"] = Math.Round(s.P95Ms, 1),
                ["threshold_count"] = thresholdCount, ["threshold_p95_ms"] = thresholdP95Ms,
                ["breaching"] = s.Count >= thresholdCount && s.P95Ms >= thresholdP95Ms,
            });
        return array;
    }
}
