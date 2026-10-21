using System.Text.Json.Nodes;
using QuantAssistant.Api.Services;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class MarketFreshnessTests
{
    [Theory]
    [InlineData("invalid","invalid")]
    [InlineData("unavailable","unavailable")]
    [InlineData("stale","stale")]
    [InlineData("unrecognized","invalid")]
    public void NativeSectorTimeRecalculationCannotPromoteRejectedOrFallbackQuotes(string prior,string expected)
    {
        var row=new JsonObject{["source"]="eastmoney",["category"]="industry",["code"]="BK0001",["change_rate"]="0",["quality_status"]=prior,["quality_reason"]="provider_validation_failed",["source_time"]="2026-10-20T10:00:00+08:00"};
        var data=new JsonObject{["sector_rankings"]=new JsonArray(row)};
        PublicDataQuality.Apply("market.snapshot",data,DateTimeOffset.Parse("2026-10-20T10:00:00+08:00"));
        Assert.Equal(expected,row["quality_status"]!.GetValue<string>());
    }
    [Fact]
    public void LegacySinaReceiptClockCannotBecomeAnObservationClock()
    {
        var fetched="2026-10-20T10:00:00+08:00";
        var row=new JsonObject{["source"]="sina",["category"]="industry",["code"]="s1",["change_rate"]="0",["observed_at"]=fetched,["source_time"]=fetched,["fetched_at"]=fetched,["quality_status"]="valid"};
        PublicDataQuality.Apply("market.snapshot",new JsonObject{["sector_rankings"]=new JsonArray(row)},DateTimeOffset.Parse(fetched));
        Assert.Null(row["source_time"]);Assert.Null(row["observed_at"]);Assert.Null(row["updated_at"]);
        Assert.Equal(fetched,row["fetched_at"]!.GetValue<string>());Assert.Equal("unknown_time",row["quality_status"]!.GetValue<string>());
    }
    [Fact]
    public void LegacyCatalogRowCountsCannotBeAdvertisedAsAuthoritativeCoverage()
    {
        var data = JsonNode.Parse("""{"indices":[],"fund_rankings":[],"sector_rankings":[],"sector_catalogs":[{"source":"sina","category":"industry","total":25,"received":25,"complete":true,"observed_at":null,"fetched_at":"2026-10-18T09:00:00+08:00"}]}""")!.AsObject();
        PublicDataQuality.Apply("market.snapshot", data, DateTimeOffset.Parse("2026-10-18T10:00:00+08:00"));
        var catalog = data["sector_catalogs"]![0]!;
        Assert.Null(catalog["total"]);
        Assert.Null(catalog["complete"]);
        Assert.Equal(25, catalog["received"]!.GetValue<int>());
        Assert.Equal("unknown", catalog["catalog_status"]!.GetValue<string>());
        Assert.Null(catalog["source_time"]);
    }
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
    [InlineData("2026-10-16T01:30:00Z", true, "continuous_trading")]
    [InlineData("2026-10-16T03:30:00Z", true, "lunch_break")]
    [InlineData("2026-10-16T05:00:00Z", true, "continuous_trading")]
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
        data["indices"] = new JsonArray(new JsonObject { ["symbol"] = "000300", ["source"] = "synthetic", ["price"] = "100" });
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
