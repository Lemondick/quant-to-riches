using System.Text.Json.Nodes;

namespace QuantAssistant.Core.Market;

public sealed record NavFreshnessResult(string Policy, string Status, int? Age, bool Stale, string? Reason, int? PublicationLag = null);

public static class NavFreshness
{
    private static readonly Lazy<JsonObject> policies = new(() =>
    {
        using var resource = typeof(NavFreshness).Assembly.GetManifestResourceStream("QuantAssistant.NavFreshnessPolicies")
            ?? throw new InvalidOperationException("NAV policy resource missing");
        return JsonNode.Parse(resource)!["policies"]!.AsObject();
    });

    public static string PolicyForFund(string? fundType, string? frequency = null)
    {
        var kind = fundType?.Trim().ToUpperInvariant() ?? "";
        if (frequency == "weekly") return "weekly";
        if (kind.StartsWith("QDII", StringComparison.Ordinal)) return "qdii_t3";
        return frequency == "daily" || new[] { "股票型", "混合型", "债券型" }.Any(prefix => kind.StartsWith(prefix, StringComparison.Ordinal)) ? "daily_t1" : "unknown";
    }

    public static NavFreshnessResult Evaluate(DateOnly day, DateTimeOffset now, MarketCalendars calendars, string policy, int? weekday = null, string market = "CN", int? publicationLag = null)
    {
        var local = MarketSessions.Local(now, market) ?? now.ToUniversalTime();
        NavFreshnessResult Unknown(string reason, int? age = null) => new(policy, "unknown", age, day < DateOnly.FromDateTime(local.DateTime), reason);
        if (policies.Value[policy] is not JsonObject config) return Unknown("nav_publication_policy_unknown");
        if (market != "CN") return Unknown("calendar_or_session_unavailable");
        var cutoff = local.TimeOfDay >= TimeSpan.FromHours(15) ? now : new DateTimeOffset(local.Date, local.Offset);
        var completed = MarketSessions.Latest(cutoff, calendars, market, false);
        if (completed is null) return Unknown("calendar_or_session_unavailable");
        if (policy == "weekly" && (weekday is null or < 0 or > 6)) return Unknown("nav_weekly_schedule_unknown");
        var count = 0;
        for (var current = day; current < completed;)
        {
            current = current.AddDays(1);
            var trading = calendars.TradingDay(market, current);
            if (trading is null) return Unknown("calendar_or_session_unavailable");
            if (trading.Value) count++;
        }
        bool stale;
        int? appliedLag = null;
        if (policy == "weekly")
        {
            var lag = publicationLag ?? config["publication_lag_sessions"]?.GetValue<int>();
            if (lag is null or < 0 or > 10) return Unknown("nav_weekly_schedule_unknown", count);
            appliedLag = lag;
            var due = completed.Value;
            while (lag > 0)
            {
                due = due.AddDays(-1);
                var trading = calendars.TradingDay(market, due);
                if (trading is null) return Unknown("calendar_or_session_unavailable", count);
                if (trading.Value) lag--;
            }
            var expected = due.AddDays(-(((int)due.DayOfWeek + 6) % 7 - weekday!.Value + 7) % 7);
            var found = false;
            for (var index = 0; index < 370; index++)
            {
                var trading = calendars.TradingDay(market, expected);
                if (trading is null) return Unknown("calendar_or_session_unavailable", count);
                if (trading.Value) { found = true; break; }
                expected = expected.AddDays(-1);
            }
            if (!found) return Unknown("calendar_or_session_unavailable", count);
            stale = day < expected;
        }
        else
        {
            if (config["max_age_sessions"] is not JsonValue number || !number.TryGetValue<int>(out var maximum) || maximum is < 0 or > 30)
                return Unknown("nav_publication_policy_unknown", count);
            stale = count > maximum;
        }
        return new(policy, stale ? "stale" : "fresh", count, stale, stale ? "nav_publication_overdue" : null, appliedLag);
    }
}
