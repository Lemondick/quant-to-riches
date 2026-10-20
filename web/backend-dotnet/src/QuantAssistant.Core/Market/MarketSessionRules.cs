using System.Collections.Frozen;
using System.Globalization;
using System.Text.Json;

namespace QuantAssistant.Core.Market;

internal sealed record SessionPeriod(int Start, int End, string Phase);
internal static class MarketSessionRules
{
    private sealed record Root(int FormatVersion, string Revision, Definition[] Markets);
    private sealed record Definition(string Market, string Source, int[] Years, string[][] Regular, string[][] HalfDay, string[] HalfDays);
    private sealed record Rule(IReadOnlySet<int> Years, IReadOnlyList<SessionPeriod> Regular, IReadOnlyList<SessionPeriod> HalfDay, IReadOnlySet<DateOnly> HalfDays);
    private static readonly Lazy<FrozenDictionary<string, Rule>> Rules = new(Load);
    private static IReadOnlyList<SessionPeriod> Parse(string[][] rows)
    {
        var result = new List<SessionPeriod>(); var previous = 0;
        int Minute(string value) => value == "24:00" ? 1440 : TimeOnly.ParseExact(value, "HH:mm", CultureInfo.InvariantCulture).Hour * 60 + TimeOnly.ParseExact(value, "HH:mm", CultureInfo.InvariantCulture).Minute;
        foreach (var row in rows)
        {
            if (row.Length != 3) throw new InvalidOperationException("Invalid session interval");
            var start = Minute(row[0]); var end = Minute(row[1]);
            if (start != previous || end <= start || row[2] is not ("pre_market" or "auction" or "continuous_trading" or "lunch_break" or "closed"))
                throw new InvalidOperationException("Invalid session intervals");
            result.Add(new(start, end, row[2])); previous = end;
        }
        if (previous != 1440) throw new InvalidOperationException("Incomplete session intervals");
        return result.AsReadOnly();
    }
    private static FrozenDictionary<string, Rule> Load()
    {
        using var resource = typeof(MarketSessionRules).Assembly.GetManifestResourceStream("QuantAssistant.ExchangeSessions")
            ?? throw new InvalidOperationException("Session authority missing");
        var root = JsonSerializer.Deserialize<Root>(resource, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower })
            ?? throw new InvalidOperationException("Session authority missing");
        if (root.FormatVersion != 1 || string.IsNullOrWhiteSpace(root.Revision)) throw new InvalidOperationException("Unsupported session authority");
        return root.Markets.Select(row =>
        {
            if (!row.Source.StartsWith("https://", StringComparison.Ordinal)) throw new InvalidOperationException("Invalid session source");
            return new KeyValuePair<string, Rule>(row.Market, new(row.Years.ToFrozenSet(), Parse(row.Regular), row.HalfDay.Length > 0 ? Parse(row.HalfDay) : [], row.HalfDays.Select(day => DateOnly.ParseExact(day, "yyyy-MM-dd", CultureInfo.InvariantCulture)).ToFrozenSet()));
        }).ToFrozenDictionary();
    }
    internal static IReadOnlyList<SessionPeriod>? For(string market, DateOnly day)
    {
        if (!Rules.Value.TryGetValue(market, out var row) || row.Years.Count > 0 && !row.Years.Contains(day.Year)) return null;
        return row.HalfDays.Contains(day) ? row.HalfDay : row.Regular;
    }
}
