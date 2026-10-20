using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Market;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class GlobalMarketSessionsTests
{
    public static IEnumerable<object[]> Sessions()
    {
        using var data = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "global-market-sessions.json")));
        foreach (var row in data.RootElement.GetProperty("sessions").EnumerateArray())
            yield return new object[] { row.GetProperty("market").GetString()!, row.GetProperty("now").GetString()!, row.GetProperty("phase").GetString()!, row.GetProperty("latest").GetString()! };
    }
    [Theory, MemberData(nameof(Sessions))]
    public void VerifiedMarketScheduleIncludesHalfDaysHolidaysAndDst(string market, string instant, string expected, string latest)
    {
        var now = DateTimeOffset.Parse(instant);
        Assert.Equal(expected, MarketSessions.Classify(now, MarketCalendars.Builtin, market));
        Assert.Equal(DateOnly.Parse(latest), MarketSessions.Latest(now, MarketCalendars.Builtin, market, false));
    }
    [Fact]
    public void QualityUsesOwnMarketBoundaryInsteadOfChineseClock()
    {
        using var data = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "global-market-sessions.json")));
        foreach (var row in data.RootElement.GetProperty("quality").EnumerateArray())
        {
            var actual = new JsonObject { ["market"] = row.GetProperty("market").GetString() };
            PublicDataQuality.Stamp(actual, "realtime_quote", "index", row.GetProperty("source").GetString(), DateTimeOffset.Parse(row.GetProperty("now").GetString()!));
            Assert.Equal(row.GetProperty("status").GetString(), actual["quality_status"]!.GetValue<string>());
            Assert.Equal(row.GetProperty("source").GetString(), actual["source_time"]!.GetValue<string>());
        }
    }
    [Fact]
    public void MissingCalendarYearAndMarketStayUnknown()
    {
        var now = DateTimeOffset.Parse("2026-10-20T10:00:00+08:00");
        Assert.Equal("unknown", MarketSessions.Classify(now, MarketCalendars.Empty, "HK"));
        Assert.Null(MarketSessions.Latest(now, MarketCalendars.Empty, "US", false));
        Assert.Equal("unknown", MarketSessions.Classify(now, MarketCalendars.Builtin, "GB"));
        Assert.Equal("unknown", MarketSessions.Classify(now.AddYears(2), MarketCalendars.Builtin, "JP"));
    }
}
