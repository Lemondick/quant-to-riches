using System.Collections.Concurrent;
using System.Diagnostics;

namespace QuantAssistant.Infrastructure.Db;

public sealed class PerformanceTrace(string traceId)
{
    private static readonly AsyncLocal<PerformanceTrace?> Ambient = new();
    private readonly ConcurrentDictionary<string, double> timings = new(StringComparer.Ordinal);
    public string TraceId { get; } = traceId;
    public static PerformanceTrace? Current { get => Ambient.Value; set => Ambient.Value = value; }
    public IReadOnlyDictionary<string, double> Timings => timings;
    public void Record(string stage, double milliseconds)
    {
        if (double.IsFinite(milliseconds) && milliseconds >= 0)
            timings.AddOrUpdate(stage, milliseconds, (_, previous) => previous + milliseconds);
    }
    public static IDisposable Measure(string stage) => new Measurement(Current, stage);
    private sealed class Measurement(PerformanceTrace? trace, string stage) : IDisposable
    {
        private readonly long began = Stopwatch.GetTimestamp();
        public void Dispose() => trace?.Record(stage, Stopwatch.GetElapsedTime(began).TotalMilliseconds);
    }
}
