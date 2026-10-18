namespace QuantAssistant.Core.Portfolio;

public static class DcaRules
{
    /// <summary>
    /// 基于均线偏离度计算智能定投倍数
    /// 偏差率 = (当前价 - 均线价) / 均线价
    /// </summary>
    public static decimal CalculateMultiplier(
        decimal currentPrice,
        decimal movingAveragePrice,
        decimal minMultiplier = 0.5m,
        decimal maxMultiplier = 2.0m,
        decimal deviationThreshold = 0.20m) // 默认 20%
    {
        if (movingAveragePrice <= 0) return 1.0m;

        var deviation = (currentPrice - movingAveragePrice) / movingAveragePrice;

        // 如果偏离度 >= 20%，此时市场过热，输出 minMultiplier
        // 如果偏离度 <= -20%，此时市场低估，输出 maxMultiplier
        // 中间做线性插值
        
        if (deviation >= deviationThreshold)
            return minMultiplier;
            
        if (deviation <= -deviationThreshold)
            return maxMultiplier;

        // 线性插值公式
        // multiplier = 1.0 - (deviation / threshold) * (max - 1.0) or something similar
        // Let's use a simpler mapping:
        // When deviation = 0, multiplier = 1.0
        // When deviation > 0, interpolate between 1.0 and minMultiplier
        // When deviation < 0, interpolate between 1.0 and maxMultiplier
        
        if (deviation > 0)
        {
            var ratio = deviation / deviationThreshold; // 0 to 1
            return 1.0m - ratio * (1.0m - minMultiplier);
        }
        else
        {
            var ratio = -deviation / deviationThreshold; // 0 to 1
            return 1.0m + ratio * (maxMultiplier - 1.0m);
        }
    }
}
