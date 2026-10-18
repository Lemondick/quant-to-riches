using System.Text.Json.Nodes;
using QuantAssistant.Api.Services;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class PublicDataQualityTests
{
    private static readonly DateTimeOffset Now = DateTimeOffset.Parse("2026-10-18T10:00:00+08:00");
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
