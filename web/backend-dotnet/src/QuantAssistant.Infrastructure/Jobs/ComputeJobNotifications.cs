using System.Collections.Concurrent;
using System.Threading.Channels;
using System.Diagnostics;

namespace QuantAssistant.Infrastructure.Jobs;

/// <summary>Disposable subscriptions; durable job reads remain authoritative.</summary>
public sealed class ComputeJobNotifications
{
    public static ComputeJobNotifications Local { get; } = new();
    private readonly ConcurrentDictionary<string, ConcurrentDictionary<Subscription, byte>> listeners = new();
    public Subscription Subscribe(string userId, string jobId) => Subscribe(userId + ":" + jobId);
    public Subscription SubscribeQueue() => Subscribe("queue");
    private Subscription Subscribe(string key)
    {
        var subscription = new Subscription();
        lock (listeners) listeners.GetOrAdd(key, _ => new())[subscription] = 0;
        subscription.OnDispose = () =>
        {
            lock (listeners)
                if (listeners.TryGetValue(key, out var group))
                {
                    group.TryRemove(subscription, out _);
                    if (group.IsEmpty) listeners.TryRemove(key, out _);
                }
        };
        return subscription;
    }
    public void Publish(string userId, string jobId) => Publish(userId + ":" + jobId);
    public void PublishQueue() => Publish("queue");
    public void Reconnected()
    {
        foreach (var key in listeners.Keys) Publish(key);
    }
    private void Publish(string key)
    {
        if (listeners.TryGetValue(key, out var group))
            foreach (var subscription in group.Keys) subscription.Signal();
    }
    public sealed class Subscription : IDisposable
    {
        private readonly Channel<long> channel = Channel.CreateBounded<long>(new BoundedChannelOptions(1) { FullMode = BoundedChannelFullMode.DropWrite });
        internal Action OnDispose { get; set; } = () => { };
        internal void Signal() => channel.Writer.TryWrite(Stopwatch.GetTimestamp());
        public async Task<TimeSpan?> WaitAsync(TimeSpan recoveryInterval, CancellationToken cancellation)
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
            timeout.CancelAfter(recoveryInterval);
            try { return Stopwatch.GetElapsedTime(await channel.Reader.ReadAsync(timeout.Token)); }
            catch (OperationCanceledException) when (!cancellation.IsCancellationRequested) { return null; }
        }
        public void Dispose() { OnDispose(); channel.Writer.TryComplete(); }
    }
}
