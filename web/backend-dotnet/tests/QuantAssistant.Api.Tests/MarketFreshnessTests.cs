using System.Text.Json.Nodes;
using QuantAssistant.Api.Services;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class MarketFreshnessTests
{
    [Fact]
    public void LegacyClientsReceiveOneIndustryTaxonomyAndNeverNullPercentages()
    {
        var data = JsonNode.Parse("""{"sector_rankings":[{"source":"eastmoney","category":"industry","code":"missing","change_rate":null},{"source":"sina","category":"industry","code":"zero","change_rate":"0"},{"source":"sina","category":"concept","code":"concept","change_rate":"99"},{"source":"sina","category":"industry","code":"up","change_rate":"1.25"}]}""")!.AsObject();
        PublicMarketSnapshot.UseLegacySectors(data);
        var rows = data["sector_rankings"]!.AsArray();
        Assert.Equal(2, rows.Count);
        Assert.Equal("up", rows[0]!["code"]!.GetValue<string>());
        Assert.Equal("0", rows[1]!["change_rate"]!.GetValue<string>());
        Assert.All(rows, row => Assert.Equal("industry", row!["category"]!.GetValue<string>()));
    }

    [Theory]
    [InlineData("2026-10-16T01:30:00Z", true, "trading")]
    [InlineData("2026-10-16T03:30:00Z", true, "break")]
    [InlineData("2026-10-16T05:00:00Z", true, "trading")]
    [InlineData("2026-10-16T08:00:00Z", true, "closed")]
    [InlineData("2026-10-18T02:00:00Z", false, "closed")]
    [InlineData("2026-10-18T02:00:00Z", null, "unknown")]
    public void SessionUsesTrustedCalendarAndShanghaiClock(string instant, bool? trading, string expected)
    {
        var now = DateTimeOffset.Parse(instant);
        var day = DateOnly.FromDateTime(now.ToOffset(TimeSpan.FromHours(8)).DateTime);
        Assert.Equal(expected, PublicMarketSnapshot.SessionState(now, day, trading));
        Assert.Equal("unknown", PublicMarketSnapshot.SessionState(now, day.AddDays(-1), trading));
    }

    [Fact]
    public void SourceTimesAndIndependentCatalogFailuresSurviveCacheReads()
    {
        var store = new PublicMarketSnapshot(TimeProvider.System);
        var data = JsonNode.Parse("""{"indices":[],"fund_rankings":[],"sector_rankings":[],"unavailable":[],"stale":false,"observed_at":"2026-10-16T15:00:00+08:00","sector_catalogs":[{"source":"sina","category":"industry","observed_at":null,"fetched_at":"2026-10-18T09:00:00+08:00","stale":true,"error":"source_unavailable"}]}""")!.AsObject();
        store.Publish(data, DateTime.UtcNow);
        var read = store.Read(null).Data!;
        Assert.Null(read["sector_catalogs"]![0]!["observed_at"]);
        Assert.True(read["sector_catalogs"]![0]!["stale"]!.GetValue<bool>());
        Assert.Equal("2026-10-16T15:00:00+08:00", read["observed_at"]!.GetValue<string>());
        read["sector_catalogs"]!.AsArray().Clear();
        Assert.Single(store.Read(null).Data!["sector_catalogs"]!.AsArray());
        Assert.Equal(30, read["refresh_interval_seconds"]!.GetValue<int>());
    }
}
