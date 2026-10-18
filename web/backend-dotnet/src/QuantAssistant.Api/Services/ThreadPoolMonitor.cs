using QuantAssistant.Api.Middleware;

namespace QuantAssistant.Api.Services;

/// <summary>Sample from a dedicated thread so the observer does not queue ThreadPool work.</summary>
public sealed class ThreadPoolMonitor(NativeMetrics metrics) : IHostedService, IDisposable
{
    private readonly ManualResetEventSlim stopped = new();
    private Thread? sampler;
    public Task StartAsync(CancellationToken cancellation)
    {
        sampler = new Thread(() => { while (!stopped.Wait(10)) metrics.SampleThreadPool(); }) { IsBackground = true, Name = "quant-thread-pool-observer" };
        sampler.Start();
        return Task.CompletedTask;
    }
    public Task StopAsync(CancellationToken cancellation) { stopped.Set(); sampler?.Join(1000); return Task.CompletedTask; }
    public void Dispose() { stopped.Set(); sampler?.Join(1000); stopped.Dispose(); }
}
