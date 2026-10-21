using System.Text.Json;
using QuantAssistant.Core.Market;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class IndexRegistryTests
{
    [Fact]
    public void EmbeddedAuthority_HasFifteenUniqueOrderedDefinitions()
    {
        var rows = IndexRegistry.All;
        Assert.Equal(15, rows.Count);
        Assert.Equal(15, rows.Select(row => row.Id).Distinct().Count());
        Assert.Equal(Enumerable.Range(1, 15), rows.Select(row => row.DisplayOrder));
        Assert.All(rows, row =>
        {
            Assert.Equal(row.Market, row.CalendarId);
            Assert.Equal(MarketSessions.ZoneId(row.Market), row.Timezone);
            Assert.True(row.Enabled);
            Assert.Equal("verified", row.Quote.VerificationStatus);
            Assert.NotEmpty(row.Quote.ResponseNames);
            Assert.NotEmpty(row.Quote.EvidenceUrls);
        });
        using var resource = typeof(IndexRegistry).Assembly.GetManifestResourceStream("QuantAssistant.IndexRegistry");
        Assert.NotNull(resource);
        using var json = JsonDocument.Parse(resource!);
        Assert.Equal(15, json.RootElement.GetProperty("definitions").GetArrayLength());
    }

    [Fact]
    public void OnlyConfirmedProductsRemainAndTheRemovedCandidateIsUnknown()
    {
        Assert.All(IndexRegistry.All, row => Assert.Equal("confirmed", row.ProductStatus));
        Assert.Throws<ArgumentException>(() => IndexRegistry.Get("cn.csi.1000"));
        Assert.Throws<ArgumentException>(() => IndexRegistry.ResolveLegacy("000852"));
        Assert.Equal("000888", IndexRegistry.Get("cn.sse.return").ExchangeCode);
        var nikkei = IndexRegistry.Get("jp.nikkei225");
        Assert.Equal("JPY", nikkei.Currency);
        Assert.Equal("INDEX", nikkei.Quote.ResponseType);
        Assert.Single(IndexRegistry.All, row => row.Id == "jp.nikkei225");
    }

    [Fact]
    public void ExistingHistoryAliases_AreExplicitCapabilitiesAndUnknownDoesNotGuess()
    {
        foreach (var symbol in new[] { "000001", "399001", "399006", "000300" })
        {
            var row = IndexRegistry.ResolveLegacy(symbol);
            Assert.Equal(symbol, row.LegacyHistorySymbol);
            Assert.Equal("verified", row.History!.VerificationStatus);
        }
        Assert.Equal(15, IndexRegistry.All.Count(row => row.History is not null));
        Assert.Equal("tencent", IndexRegistry.ResolveLegacy("000888").History!.Provider);
        Assert.Equal("eastmoney", IndexRegistry.ResolveLegacy("899050").History!.Provider);
        Assert.Equal(4, IndexRegistry.All.Count(row => row.LegacyHistorySymbol is not null));
        Assert.Throws<ArgumentException>(() => IndexRegistry.ResolveLegacy("600000"));
        Assert.Throws<ArgumentException>(() => IndexRegistry.Get("us.nasdaq100"));
    }

    [Fact]
    public void JsonProjection_IsDetachedFromTheStoredAuthority()
    {
        var expected = IndexRegistry.All[0].Name;
        var projection = IndexRegistry.JsonDefinitions();
        projection[0]!["name"] = "changed";
        Assert.Equal(expected, IndexRegistry.All[0].Name);
        Assert.Equal(expected, IndexRegistry.JsonDefinitions()[0]!["name"]!.GetValue<string>());
    }
}
