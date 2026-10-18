using System.Collections.Frozen;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace QuantAssistant.Core.Market;

public sealed record CalendarYear(string Market, int Year, string Source, string Revision, IReadOnlySet<DateOnly> Holidays);

/// <summary>Read-only calendar evidence. A missing market/year is never inferred from another one.</summary>
public sealed class MarketCalendars(IEnumerable<CalendarYear> years)
{
    public static IReadOnlySet<string> Markets { get; } = new[] { "CN", "HK", "US", "JP" }.ToFrozenSet();
    private readonly FrozenDictionary<(string Market, int Year), CalendarYear> entries = years
        .GroupBy(row => (row.Market, row.Year)).Where(group => group.Count() == 1)
        .Select(group => group.Single() with { Holidays = group.Single().Holidays.ToFrozenSet() })
        .ToFrozenDictionary(row => (row.Market, row.Year));
    public static MarketCalendars Empty { get; } = new([]);
    private static readonly Lazy<MarketCalendars> builtin = new(() =>
    {
        using var resource = typeof(MarketCalendars).Assembly.GetManifestResourceStream("QuantAssistant.ExchangeCalendars")
            ?? throw new InvalidOperationException("Calendar resource missing");
        return FromJson(JsonNode.Parse(resource));
    });
    public static MarketCalendars Builtin => builtin.Value;
    public IEnumerable<CalendarYear> Years => entries.Values;
    public CalendarYear? Load(string market, int year) => entries.GetValueOrDefault((market, year));
    public bool? TradingDay(string market, DateOnly day) => Load(market, day.Year) is { } year
        ? day.DayOfWeek is not (DayOfWeek.Saturday or DayOfWeek.Sunday) && !year.Holidays.Contains(day) : null;

    public static MarketCalendars FromJson(JsonNode? value)
    {
        try
        {
            if (value is not JsonObject root || root["format_version"]?.GetValue<int>() != 1
                || root["calendars"] is not JsonArray { Count: <= 24 } rows) return Empty;
            var result = new List<CalendarYear>();
            foreach (var row in rows.OfType<JsonObject>())
            {
                try
                {
                    var market = row["market"]!.GetValue<string>(); var year = row["year"]!.GetValue<int>();
                    var source = row["source"]!.GetValue<string>(); var revision = row["revision"]!.GetValue<string>();
                    if (!Markets.Contains(market) || year is < 2000 or > 2100 || string.IsNullOrWhiteSpace(revision)
                        || !Uri.TryCreate(source, UriKind.Absolute, out var url) || url.Scheme != "https" || url.UserInfo.Length > 0
                        || row["holidays"] is not JsonArray { Count: <= 366 } raw) continue;
                    var holidays = raw.Select(day => DateOnly.ParseExact(day!.GetValue<string>(), "yyyy-MM-dd", CultureInfo.InvariantCulture)).ToFrozenSet();
                    if (holidays.Any(day => day.Year != year)) continue;
                    result.Add(new(market, year, source, revision, holidays));
                }
                catch (Exception error) when (error is InvalidOperationException or FormatException or NullReferenceException) { }
            }
            return new(result);
        }
        catch (Exception error) when (error is InvalidOperationException or JsonException) { return Empty; }
    }

    public JsonObject ToJson() => new()
    {
        ["format_version"] = 1,
        ["calendars"] = new JsonArray(entries.Values.OrderBy(row => row.Market, StringComparer.Ordinal).ThenBy(row => row.Year)
            .Select(row => (JsonNode)new JsonObject
            {
                ["market"] = row.Market, ["year"] = row.Year, ["source"] = row.Source, ["revision"] = row.Revision,
                ["holidays"] = new JsonArray(row.Holidays.Order().Select(day => (JsonNode?)JsonValue.Create(day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture))).ToArray()),
            }).ToArray()),
    };
}
