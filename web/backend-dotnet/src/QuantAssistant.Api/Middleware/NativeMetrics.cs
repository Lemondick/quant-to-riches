using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;

namespace QuantAssistant.Api.Middleware;

public sealed class NativeMetrics
{
    private readonly ConcurrentDictionary<int,long> counts=[];
    private long elapsedMilliseconds;
    private readonly long started=Stopwatch.GetTimestamp();
    private readonly object sampleLock = new();
    private readonly Queue<(long At, long Duration)> samples = new();
    private readonly Queue<(long At, long Pending)> poolSamples = new();
    public void SampleThreadPool()
    {
        lock (sampleLock)
        {
            poolSamples.Enqueue((Stopwatch.GetTimestamp(), ThreadPool.PendingWorkItemCount));
            while (poolSamples.TryPeek(out var item) && Stopwatch.GetElapsedTime(item.At).TotalSeconds > 10) poolSamples.Dequeue();
        }
    }
    public void Observe(int status,long milliseconds, bool publicRequest = true)
    {
        milliseconds = Math.Max(0, milliseconds);
        counts.AddOrUpdate(status,1,(_,old)=>old+1);
        Interlocked.Add(ref elapsedMilliseconds,milliseconds);
        if (!publicRequest) return;
        lock (sampleLock)
        {
            Trim();
            if (samples.Count == 4096) samples.Dequeue();
            samples.Enqueue((Stopwatch.GetTimestamp(), milliseconds));
        }
    }
    private void Trim()
    {
        while (samples.TryPeek(out var sample) && Stopwatch.GetElapsedTime(sample.At).TotalMinutes > 5) samples.Dequeue();
    }
    public JsonObject Snapshot()
    {
        var requests = counts.Values.Sum();
        using var process = Process.GetCurrentProcess();
        long[] durations;
        long peak; int poolCount;
        lock (sampleLock) { Trim(); durations = samples.Select(sample => sample.Duration).Order().ToArray(); peak = poolSamples.Count == 0 ? 0 : poolSamples.Max(item => item.Pending); poolCount = poolSamples.Count; }
        return new JsonObject { ["requests"] = requests, ["server_errors"] = counts.Where(pair => pair.Key >= 500).Sum(pair => pair.Value),
            ["average_duration_ms"] = requests == 0 ? null : (double?)Interlocked.Read(ref elapsedMilliseconds) / requests,
            ["p95_duration_ms"] = durations.Length == 0 ? null : (long?)durations[(int)Math.Ceiling(durations.Length * .95) - 1],
            ["duration_sample_count"] = durations.Length, ["duration_window_seconds"] = 300,
            ["thread_pool_pending"] = ThreadPool.PendingWorkItemCount, ["thread_pool_threads"] = ThreadPool.ThreadCount,
            ["thread_pool_pending_peak_10s"] = peak, ["thread_pool_sample_count_10s"] = poolCount,
            ["uptime_seconds"] = Stopwatch.GetElapsedTime(started).TotalSeconds, ["memory_bytes"] = process.WorkingSet64 };
    }
    public string Render()
    {
        var text=new StringBuilder("# TYPE quant_http_requests_total counter\n");
        foreach(var (status,count) in counts.OrderBy(p=>p.Key))text.Append(CultureInfo.InvariantCulture,$"quant_http_requests_total{{status=\"{status}\"}} {count}\n");
        text.Append(CultureInfo.InvariantCulture,$"# TYPE quant_http_duration_milliseconds_total counter\nquant_http_duration_milliseconds_total {Interlocked.Read(ref elapsedMilliseconds)}\n");
        text.Append(CultureInfo.InvariantCulture,$"# TYPE quant_process_uptime_seconds gauge\nquant_process_uptime_seconds {Stopwatch.GetElapsedTime(started).TotalSeconds:F3}\n");
        var snapshot = Snapshot();
        text.Append(CultureInfo.InvariantCulture,$"# TYPE quant_thread_pool_pending gauge\nquant_thread_pool_pending {ThreadPool.PendingWorkItemCount}\n");
        text.Append(CultureInfo.InvariantCulture,$"# TYPE quant_thread_pool_pending_peak_10s gauge\nquant_thread_pool_pending_peak_10s {snapshot["thread_pool_pending_peak_10s"]}\n# TYPE quant_thread_pool_sample_count_10s gauge\nquant_thread_pool_sample_count_10s {snapshot["thread_pool_sample_count_10s"]}\n");
        if (snapshot["p95_duration_ms"] is not null)
            text.Append(CultureInfo.InvariantCulture,$"# TYPE quant_http_recent_p95_milliseconds gauge\nquant_http_recent_p95_milliseconds {snapshot["p95_duration_ms"]}\n");
        using var process=Process.GetCurrentProcess();text.Append(CultureInfo.InvariantCulture,$"# TYPE quant_process_memory_bytes gauge\nquant_process_memory_bytes {process.WorkingSet64}\n");return text.ToString();
    }
}
public sealed class NativeMetricsMiddleware(RequestDelegate next)
{
    public Task InvokeAsync(HttpContext context,NativeMetrics metrics)
    {var began=Stopwatch.GetTimestamp();
        context.Response.OnStarting(()=>{if(context.Request.Path.StartsWithSegments("/api/v1/market") || (context.Request.Path.StartsWithSegments("/api/v1/funds") && context.Request.Path.Value!.EndsWith("/detail",StringComparison.Ordinal)))
            context.Response.Headers.Append("Server-Timing",$"app;dur={Stopwatch.GetElapsedTime(began).TotalMilliseconds.ToString("F2",CultureInfo.InvariantCulture)}");return Task.CompletedTask;});
        context.Response.OnCompleted(()=>{metrics.Observe(context.Response.StatusCode,(long)Stopwatch.GetElapsedTime(began).TotalMilliseconds,
        !context.Request.Path.StartsWithSegments("/internal") && context.Request.Path != "/metrics");return Task.CompletedTask;});return next(context);}
}
