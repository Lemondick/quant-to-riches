namespace QuantAssistant.Core.Portfolio;

public static class BrinsonModel
{
    public static List<AttributionDetail> Aggregate(IReadOnlyCollection<BrinsonHolding> portfolio, IReadOnlyCollection<BrinsonHolding> benchmark)
    {
        static Dictionary<string, (decimal Weight, decimal Return)> Side(IReadOnlyCollection<BrinsonHolding> holdings)
        {
            if (holdings.Count is < 1 or > 121 || holdings.Any(h => string.IsNullOrWhiteSpace(h.Symbol) || string.IsNullOrWhiteSpace(h.SectorName) || h.SectorName.Length > 80 || h.SectorName.Any(char.IsControl) || h.InitialValue is <= 0 or >= 100000000000000000000m || h.PeriodReturn is < -1 or > 1000000)
                || holdings.Select(h => h.Symbol).Distinct(StringComparer.OrdinalIgnoreCase).Count() != holdings.Count)
                throw new ArgumentException("自动归因的标的证据无效。");
            var groups = holdings.GroupBy(h => h.SectorName.Trim(), StringComparer.OrdinalIgnoreCase)
                .Select(g => (Name: g.Key, Value: g.Sum(h => h.InitialValue), Return: g.Sum(h => h.InitialValue * h.PeriodReturn) / g.Sum(h => h.InitialValue)))
                .OrderBy(g => g.Value).ThenBy(g => g.Name, StringComparer.Ordinal).ToArray();
            var total = groups.Sum(g => g.Value);
            var result = new Dictionary<string, (decimal Weight, decimal Return)>(StringComparer.OrdinalIgnoreCase);
            decimal used = 0;
            for (var i = 0; i < groups.Length; i++)
            {
                // Absorb division rounding in the largest sector so both sides sum exactly to one.
                var weight = i == groups.Length - 1 ? 1m - used : groups[i].Value / total;
                result.Add(groups[i].Name, (weight, groups[i].Return));
                used += weight;
            }
            return result;
        }
        var p = Side(portfolio); var b = Side(benchmark);
        var details = p.Keys.Union(b.Keys, StringComparer.OrdinalIgnoreCase).Order(StringComparer.Ordinal).Select(sector =>
        {
            var ps = p.GetValueOrDefault(sector); var bs = b.GetValueOrDefault(sector);
            return new AttributionDetail { SectorName = sector, PortfolioWeight = ps.Weight, BenchmarkWeight = bs.Weight, PortfolioReturn = ps.Return, BenchmarkReturn = bs.Return };
        }).ToList();
        CalculateEffects(details);
        return details;
    }

    /// <summary>
    /// 基于 Brinson-Fachler 模型的单期归因计算
    /// 资产配置效应 (Allocation) = (W_p - W_b) * (R_b - R_b_total)
    /// 个券选择效应 (Selection) = W_b * (R_p - R_b)
    /// 交互效应 (Interaction) = (W_p - W_b) * (R_p - R_b)
    /// </summary>
    public static void CalculateEffects(List<AttributionDetail> details)
    {
        if (details.Count == 0) return;
        
        // 基准总收益率 R_b_total = Sum(W_b * R_b)
        var benchmarkTotalReturn = details.Sum(d => d.BenchmarkWeight * d.BenchmarkReturn);
        
        foreach (var d in details)
        {
            d.Allocation = (d.PortfolioWeight - d.BenchmarkWeight) * (d.BenchmarkReturn - benchmarkTotalReturn);
            d.Selection = d.BenchmarkWeight * (d.PortfolioReturn - d.BenchmarkReturn);
            d.Interaction = (d.PortfolioWeight - d.BenchmarkWeight) * (d.PortfolioReturn - d.BenchmarkReturn);
        }
    }
}
