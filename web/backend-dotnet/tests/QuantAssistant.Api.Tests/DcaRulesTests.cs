using QuantAssistant.Core.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class DcaRulesTests
{
    [Theory]
    [InlineData(100, 100, 1.0)] // deviation 0 -> multiplier 1.0
    [InlineData(120, 100, 0.5)] // deviation +20% -> min multiplier 0.5
    [InlineData(80, 100, 2.0)]  // deviation -20% -> max multiplier 2.0
    [InlineData(110, 100, 0.75)] // deviation +10% -> half way to min (1.0 - 0.5 * 0.5 = 0.75)
    [InlineData(90, 100, 1.5)]  // deviation -10% -> half way to max (1.0 + 0.5 * 1.0 = 1.5)
    [InlineData(130, 100, 0.5)] // deviation +30% -> capped at min 0.5
    [InlineData(70, 100, 2.0)]  // deviation -30% -> capped at max 2.0
    [InlineData(0, 100, 2.0)]   // edge case: price drops to 0
    [InlineData(100, 0, 1.0)]   // edge case: MA price is 0
    public void CalculateMultiplier_ReturnsExpectedValues(decimal current, decimal ma, decimal expected)
    {
        var result = DcaRules.CalculateMultiplier(current, ma, 0.5m, 2.0m, 0.20m);
        Assert.Equal(expected, result);
    }
}
