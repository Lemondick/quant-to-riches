using QuantAssistant.Core.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class BrinsonModelTests
{
    [Fact]
    public void CalculateEffects_ComputesCorrectly()
    {
        var details = new List<AttributionDetail>
        {
            // Example data based on a classic Brinson-Fachler case
            new AttributionDetail
            {
                SectorName = "Tech",
                PortfolioWeight = 0.50m,
                BenchmarkWeight = 0.40m,
                PortfolioReturn = 0.10m,
                BenchmarkReturn = 0.08m
            },
            new AttributionDetail
            {
                SectorName = "Finance",
                PortfolioWeight = 0.50m,
                BenchmarkWeight = 0.60m,
                PortfolioReturn = 0.05m,
                BenchmarkReturn = 0.04m
            }
        };

        BrinsonModel.CalculateEffects(details);

        // Benchmark Total Return = (0.40 * 0.08) + (0.60 * 0.04) = 0.032 + 0.024 = 0.056
        var tech = details.Single(d => d.SectorName == "Tech");
        
        // Allocation = (W_p - W_b) * (R_b - R_b_total) = (0.50 - 0.40) * (0.08 - 0.056) = 0.10 * 0.024 = 0.0024
        Assert.Equal(0.0024m, tech.Allocation);
        
        // Selection = W_b * (R_p - R_b) = 0.40 * (0.10 - 0.08) = 0.40 * 0.02 = 0.0080
        Assert.Equal(0.0080m, tech.Selection);
        
        // Interaction = (W_p - W_b) * (R_p - R_b) = (0.50 - 0.40) * (0.10 - 0.08) = 0.10 * 0.02 = 0.0020
        Assert.Equal(0.0020m, tech.Interaction);

        var finance = details.Single(d => d.SectorName == "Finance");
        
        // Allocation = (-0.10) * (0.04 - 0.056) = -0.10 * -0.016 = 0.0016
        Assert.Equal(0.0016m, finance.Allocation);
        
        // Selection = 0.60 * (0.05 - 0.04) = 0.60 * 0.01 = 0.0060
        Assert.Equal(0.0060m, finance.Selection);
        
        // Interaction = (-0.10) * (0.01) = -0.0010
        Assert.Equal(-0.0010m, finance.Interaction);
    }
}
