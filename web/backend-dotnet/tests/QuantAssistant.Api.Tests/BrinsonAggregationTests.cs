using QuantAssistant.Core.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class BrinsonAggregationTests
{
    [Fact]
    public void Aggregation_UsesBeginningValuesAndUnionOfSectors()
    {
        var details = BrinsonModel.Aggregate([
            new("A", "科技", 200, 0.1m), new("B", "科技", 300, 0.2m), new("CASH", "现金", 500, 0)
        ], [new("A", "科技", 0.4m, 0.1m), new("C", "金融", 0.6m, 0.05m)]);
        Assert.Equal(3, details.Count);
        var technology = Assert.Single(details, d => d.SectorName == "科技");
        Assert.Equal(0.5m, technology.PortfolioWeight);
        Assert.Equal(0.16m, technology.PortfolioReturn);
        Assert.Equal(0m, Assert.Single(details, d => d.SectorName == "金融").PortfolioWeight);
        Assert.Equal(0.01m, details.Sum(d => d.Allocation + d.Selection + d.Interaction));
    }

    [Fact]
    public void RepeatingDecimalWeights_SumExactlyToOneAndPreserveIdentity()
    {
        var details = BrinsonModel.Aggregate([new("A", "甲", 1, 0.1m), new("B", "乙", 1, -0.2m), new("C", "丙", 1, 0.3m)],
            [new("D", "甲", 2, 0.04m), new("E", "乙", 3, 0.05m), new("F", "丙", 4, 0.06m)]);
        Assert.Equal(1m, details.Sum(d => d.PortfolioWeight));
        Assert.Equal(1m, details.Sum(d => d.BenchmarkWeight));
        var active = details.Sum(d => d.PortfolioWeight * d.PortfolioReturn - d.BenchmarkWeight * d.BenchmarkReturn);
        Assert.InRange(Math.Abs(active - details.Sum(d => d.Allocation + d.Selection + d.Interaction)), 0m, 0.00000000000000000000000001m);
    }
}
