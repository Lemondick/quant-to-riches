namespace QuantAssistant.Api.Services;

internal sealed record PublicRefreshSettings(TimeSpan Interval, TimeSpan Deadline, TimeSpan TaskTimeout)
{
    internal static PublicRefreshSettings From(IConfiguration? configuration)
    {
        int Seconds(string name, int fallback, int minimum, int maximum) =>
            int.TryParse(configuration?[name], out var value) ? Math.Clamp(value, minimum, maximum) : fallback;
        var interval = Seconds("QUANT_PUBLIC_REFRESH_INTERVAL_SECONDS", 30, 15, 300);
        var deadline = Seconds("QUANT_PUBLIC_REFRESH_DEADLINE_SECONDS", 75, 10, 180);
        var task = Math.Min(Seconds("QUANT_PUBLIC_REFRESH_TASK_TIMEOUT_SECONDS", 55, 5, 120), deadline);
        return new(TimeSpan.FromSeconds(interval), TimeSpan.FromSeconds(deadline), TimeSpan.FromSeconds(task));
    }
}
