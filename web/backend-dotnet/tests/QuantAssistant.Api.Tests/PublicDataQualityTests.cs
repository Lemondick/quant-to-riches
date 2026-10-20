using System.Text.Json.Nodes;
using System.Text.Json;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Market;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class PublicDataQualityTests
{
    [Fact]
    public void VerifiedAuctionCapabilitySurvivesNativeReadAdapter()
    {
        var data = JsonNode.Parse("""{"items":[{"symbol":"000300","asset_type":"index","source":"synthetic-verified-contract","trade_time_verified":true,"auction_supported":true,"observed_at":"2026-10-19T09:20:00+08:00"}]}""")!.AsObject();
        PublicDataQuality.Apply("market.quotes", data, DateTimeOffset.Parse("2026-10-19T09:20:00+08:00"));
        Assert.Equal("valid", data["items"]![0]!["quality_status"]!.GetValue<string>());
    }
    [Theory]
    [InlineData("2026-10-19T09:20:00+08:00", "2026-10-19T09:20:00+08:00", true, "valid")]
    [InlineData("2026-10-19T09:20:00+08:00", "2026-10-19T09:20:00+08:00", false, "unavailable")]
    [InlineData("2026-10-19T09:20:00+08:00", "2026-10-16T15:00:00+08:00", true, "stale")]
    [InlineData("2026-10-19T09:20:00+08:00", "2026-10-16T15:00:00+08:00", false, "valid")]
    [InlineData("2026-10-19T09:29:59+08:00", "2026-10-19T09:25:00+08:00", true, "valid")]
    [InlineData("2026-10-19T09:30:00+08:00", "2026-10-16T15:00:00+08:00", false, "stale")]
    [InlineData("2026-10-19T12:00:00+08:00", "2026-10-19T11:30:00+08:00", false, "valid")]
    [InlineData("2026-10-19T15:05:00+08:00", "2026-10-19T15:00:00+08:00", false, "valid")]
    [InlineData("2026-10-18T10:00:00+08:00", "2026-10-16T10:00:00+08:00", false, "stale")]
    public void AuctionRequiresSourceCapabilityAndDoesNotForgiveStaleClocks(string now, string value, bool supports, string expected)
    {
        var row = new JsonObject();
        PublicDataQuality.Stamp(row, "realtime_quote", "index", value, DateTimeOffset.Parse(now), auctionSupported: supports);
        Assert.Equal(expected, row["quality_status"]!.GetValue<string>());
        Assert.Equal(value, row["source_time"]!.GetValue<string>());
    }

    [Fact]
    public void TemporaryClosureRevisionOverridesBuiltinAndCrossYearUnknownStopsTraversal()
    {
        var calendar = new MarketCalendars([new("CN", 2026, "https://www.sse.com.cn/test", "temporary", new HashSet<DateOnly> { new(2026, 10, 19) })]);
        var row = new JsonObject();
        PublicDataQuality.Stamp(row, "realtime_quote", "index", "2026-10-16T15:00:00+08:00", DateTimeOffset.Parse("2026-10-19T10:00:00+08:00"), calendar);
        Assert.Equal("valid", row["quality_status"]!.GetValue<string>());
        Assert.Equal("closed", row["market_session"]!.GetValue<string>());
        Assert.Equal("temporary", row["calendar_revision"]!.GetValue<string>());
        Assert.Null(MarketSessions.Latest(DateTimeOffset.Parse("2026-01-01T09:20:00+08:00"), MarketCalendars.Builtin, "CN", true));
    }
    public static IEnumerable<object?[]> Sessions()
    {
        using var fixture = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "public-quality-cases.json")));
        foreach (var row in fixture.RootElement.GetProperty("sessions").EnumerateArray())
            yield return [row.GetProperty("now").GetString(), row.GetProperty("trading").ValueKind == JsonValueKind.Null ? null : row.GetProperty("trading").GetBoolean(), row.GetProperty("phase").GetString()];
    }

    public static IEnumerable<object[]> Timestamps()
    {
        using var fixture = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "public-quality-cases.json")));
        foreach (var row in fixture.RootElement.GetProperty("timestamps").EnumerateArray())
            yield return [row.GetProperty("now").GetString()!, row.GetProperty("value").GetString()!, row.GetProperty("quality").GetString()!];
    }

    [Theory, MemberData(nameof(Sessions))]
    public void SessionStatesUseExchangeBoundaries(string now, bool? trading, string expected)
    {
        var instant = DateTimeOffset.Parse(now);
        Assert.Equal(expected, PublicMarketSnapshot.SessionState(instant, DateOnly.FromDateTime(instant.DateTime), trading));
    }

    [Theory, MemberData(nameof(Timestamps))]
    public void SourceTimestampsPreserveExplicitOffsetsAndRejectInvalidValues(string now, string value, string expected)
    {
        var row = new JsonObject();
        PublicDataQuality.Stamp(row, "historical_quote", "index", value, DateTimeOffset.Parse(now));
        Assert.Equal(expected, row["quality_status"]!.GetValue<string>());
        Assert.Equal(expected == "valid" ? value : null, row["source_time"]?.GetValue<string>());
    }

    private static readonly DateTimeOffset Now = DateTimeOffset.Parse("2026-10-18T10:00:00+08:00");
    [Fact]
    public void UnconfiguredOverseasMarketDoesNotUseMainlandCalendar()
    {
        var row = new JsonObject { ["market"] = "HK" };
        PublicDataQuality.Stamp(row, "realtime_quote", "index", "2026-10-16T15:00:00+08:00", Now, calendars: MarketCalendars.Empty);
        Assert.Equal("unknown_time", row["quality_status"]!.GetValue<string>());
        Assert.Equal("HK", row["market"]!.GetValue<string>());
        Assert.Equal("2026-10-16T15:00:00+08:00", row["source_time"]!.GetValue<string>());
    }
    [Fact]
    public void OldOfficialNavCache_IsSeparated_AndLegacyClientsNeverReceiveNullAsZero()
    {
        var data = JsonNode.Parse("""{"items":[{"symbol":"000001","estimate_nav":"1.106","change_rate":"-0.09","change_amount":"-0.001","observed_at":"2026-10-18T09:00:00+08:00","valuation_type":"latest_nav","nav_date":"2026-10-16","source":"local:eastmoney"}],"unavailable":[]}""")!.AsObject();
        PublicDataQuality.Apply("fund.valuations", data, Now);
        var row = data["items"]![0]!;
        Assert.Null(row["estimate_nav"]);
        Assert.Null(row["source_time"]);
        Assert.Equal("1.106", row["official_nav"]!.GetValue<string>());
        Assert.Equal("2026-10-16", row["updated_at"]!.GetValue<string>());
        PublicDataQuality.Apply("fund.valuations", data, Now.AddMinutes(1), false);
        Assert.Empty(data["items"]!.AsArray());
        Assert.Equal("000001", data["unavailable"]![0]!.GetValue<string>());
    }

    [Fact]
    public void CacheReadPreservesSourceClock_AndUnknownTimeDoesNotUseFetchClock()
    {
        var data = JsonNode.Parse("""{"items":[{"symbol":"000001","asset_type":"index","observed_at":"2026-10-16T15:00:00+08:00","trade_time_verified":true,"source":"tencent"},{"symbol":"399001","asset_type":"index","observed_at":"2026-10-18T09:59:59+08:00","trade_time_verified":false,"source":"tencent"}]}""")!.AsObject();
        PublicDataQuality.Apply("market.quotes", data, Now);
        Assert.Equal("valid", data["items"]![0]!["quality_status"]!.GetValue<string>());
        var before = data["items"]![0]!["updated_at"]!.GetValue<string>();
        PublicDataQuality.Apply("market.quotes", data, Now.AddMinutes(1));
        Assert.Equal(before, data["items"]![0]!["updated_at"]!.GetValue<string>());
        Assert.Null(data["items"]![1]!["updated_at"]);
    }

    [Theory]
    [InlineData("2026-10-05T10:00:00+08:00", "2026-09-30T15:00:00+08:00", "valid")]
    [InlineData("2026-10-18T10:00:00+08:00", "2026-10-15T15:00:00+08:00", "stale")]
    [InlineData("2026-10-18T10:00:00+08:00", "2026-10-19T15:00:00+08:00", "invalid")]
    public void ClockClassification_UsesTradingSession(string now, string source, string expected)
    {
        var row = new JsonObject();
        PublicDataQuality.Stamp(row, "realtime_quote", "index", source, DateTimeOffset.Parse(now));
        Assert.Equal(expected, row["quality_status"]!.GetValue<string>());
    }
}
