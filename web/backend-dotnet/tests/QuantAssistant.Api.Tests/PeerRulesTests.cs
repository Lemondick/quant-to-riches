using QuantAssistant.Core.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class PeerRulesTests
{
    [Fact]
    public void ComputeRankings_CalculatesRanksAndPercentilesCorrectly()
    {
        var rankings = new List<PeerRanking>
        {
            new() { Symbol = "F1", Return1M = 0.10m, Return1Y = 0.50m }, // 1st in 1M, 1st in 1Y
            new() { Symbol = "F2", Return1M = 0.05m, Return1Y = 0.20m }, // 2nd in 1M, 2nd in 1Y
            new() { Symbol = "F3", Return1M = -0.02m, Return1Y = 0.10m }, // 3rd in 1M, 4th in 1Y
            new() { Symbol = "F4", Return1M = -0.05m, Return1Y = 0.15m }, // 4th in 1M, 3rd in 1Y
            new() { Symbol = "F5", Return1M = null, Return1Y = null }    // No data
        };

        PeerRules.ComputeRankings(rankings);

        var f1 = rankings.First(r => r.Symbol == "F1");
        Assert.Equal(1, f1.Rank1M);
        Assert.Equal(100m, f1.Percentile1M);
        Assert.Equal(1, f1.Rank1Y);
        Assert.Equal(100m, f1.Percentile1Y);

        var f2 = rankings.First(r => r.Symbol == "F2");
        Assert.Equal(2, f2.Rank1M);
        Assert.Equal(66.67m, Math.Round(f2.Percentile1M!.Value, 2));
        Assert.Equal(2, f2.Rank1Y);
        Assert.Equal(66.67m, Math.Round(f2.Percentile1Y!.Value, 2));

        var f4 = rankings.First(r => r.Symbol == "F4");
        Assert.Equal(4, f4.Rank1M);
        Assert.Equal(0m, f4.Percentile1M);
        Assert.Equal(3, f4.Rank1Y);
        Assert.Equal(33.33m, Math.Round(f4.Percentile1Y!.Value, 2));

        var f5 = rankings.First(r => r.Symbol == "F5");
        Assert.Null(f5.Rank1M);
        Assert.Null(f5.Percentile1M);
    }
}
