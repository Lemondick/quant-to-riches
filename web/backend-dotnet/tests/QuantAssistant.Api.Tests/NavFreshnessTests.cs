using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Market;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class NavFreshnessTests
{
    [Theory]
    [InlineData("股票型", null, "daily_t1")]
    [InlineData("债券型-长债", null, "daily_t1")]
    [InlineData("QDII-混合型", null, "qdii_t3")]
    [InlineData("货币型", null, "unknown")]
    [InlineData("指数型", null, "unknown")]
    [InlineData("某某股票基金", null, "unknown")]
    [InlineData(null, "weekly", "weekly")]
    public void PolicyUsesStructuredTypeOrFrequency(string? kind, string? frequency, string expected) => Assert.Equal(expected, NavFreshness.PolicyForFund(kind, frequency));

    public static IEnumerable<object[]> Cases()
    {
        using var fixture = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "public-quality-cases.json")));
        foreach (var row in fixture.RootElement.GetProperty("navs").EnumerateArray()) yield return [row.GetRawText()];
    }

    [Theory, MemberData(nameof(Cases))]
    public void FormalNavUsesCompletedSessionsAndItsOwnPublicationPolicy(string json)
    {
        var value = JsonNode.Parse(json)!;
        var row = new JsonObject { ["nav_freshness_policy"] = value["policy"]!.GetValue<string>(), ["nav_valuation_weekday"] = value["weekday"]?.DeepClone() };
        PublicDataQuality.Stamp(row, "official_nav", "fund", value["date"]!.GetValue<string>(), DateTimeOffset.Parse(value["now"]!.GetValue<string>()));
        Assert.Equal(value["quality"]!.GetValue<string>(), row["quality_status"]!.GetValue<string>());
        Assert.Equal(value["stale"]!.GetValue<bool>(), row["is_stale"]!.GetValue<bool>());
        Assert.Equal(value["age"]?.GetValue<int>(), row["nav_age_sessions"]?.GetValue<int>());
        Assert.Equal(value["date"]!.GetValue<string>(), row["updated_at"]!.GetValue<string>());
        Assert.Null(row["source_time"]);
    }
}
