namespace QuantAssistant.Api.Tests;

// Deterministic timers for real hosted-service deadline and cooldown tests.
internal sealed class ManualClock(DateTimeOffset initial) : TimeProvider
{
    private readonly object gate = new();
    private DateTimeOffset now = initial;
    private readonly List<Timer> timers = [];
    public override DateTimeOffset GetUtcNow() { lock (gate) return now; }
    public override long GetTimestamp() => GetUtcNow().UtcTicks;
    public override long TimestampFrequency => TimeSpan.TicksPerSecond;
    public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
    {
        lock (gate)
        {
            var timer = new Timer(this, callback, state);
            timers.Add(timer);
            timer.Change(dueTime, period);
            return timer;
        }
    }
    internal void Advance(TimeSpan duration)
    {
        var target = GetUtcNow() + duration;
        while (true)
        {
            Timer? timer;
            lock (gate)
            {
                timer = timers.Where(t => t.Due <= target).MinBy(t => t.Due);
                if (timer is null) { now = target; return; }
                now = timer.Due!.Value;
                timer.Due = timer.Period > TimeSpan.Zero ? now + timer.Period : null;
            }
            timer.Callback(timer.State);
        }
    }
    private sealed class Timer(ManualClock clock, TimerCallback callback, object? state) : ITimer
    {
        internal DateTimeOffset? Due;
        internal TimeSpan Period;
        internal readonly TimerCallback Callback = callback;
        internal readonly object? State = state;
        private bool disposed;
        public bool Change(TimeSpan dueTime, TimeSpan period)
        {
            lock (clock.gate)
            {
                if (disposed) return false;
                Due = dueTime == Timeout.InfiniteTimeSpan ? null : clock.now + dueTime;
                Period = period;
                return true;
            }
        }
        public void Dispose() { lock (clock.gate) { disposed = true; Due = null; clock.timers.Remove(this); } }
        public ValueTask DisposeAsync() { Dispose(); return ValueTask.CompletedTask; }
    }
}
