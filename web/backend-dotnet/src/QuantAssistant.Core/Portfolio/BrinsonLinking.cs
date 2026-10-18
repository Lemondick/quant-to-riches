namespace QuantAssistant.Core.Portfolio;

public sealed record LinkedSectorEffect(string SectorName, decimal Allocation, decimal Selection, decimal Interaction);
public sealed record LinkedBrinsonResult(decimal PortfolioReturn, decimal BenchmarkReturn, decimal ActiveReturn,
    decimal Allocation, decimal Selection, decimal Interaction, decimal ReconciliationResidual, List<LinkedSectorEffect> Sectors);

public static class BrinsonLinking
{
    public static LinkedBrinsonResult Calculate(IReadOnlyList<AttributionReportView> periods)
    {
        if (periods.Count is < 2 or > 366) throw new ArgumentException("请选择2至366份单期报告。");
        var first = periods[0].Report;
        var sectors = new Dictionary<string, LinkedSectorEffect>(StringComparer.OrdinalIgnoreCase);
        decimal portfolioGross = 1, benchmarkGross = 1;
        static bool Same(decimal? saved, decimal computed) => saved is not null && Math.Abs(saved.Value - computed) <= .000000000001m;
        try
        {
            for (var index = 0; index < periods.Count; index++)
            {
                var view = periods[index]; var report = view.Report; var details = view.Details;
                if (report.Status != "completed" || report.DataSource is not ("manual_sector_input" or "native_static_price" or "native_ledger_period")
                    || report.UserId != first.UserId || report.PortfolioSymbol != first.PortfolioSymbol || report.BenchmarkSymbol != first.BenchmarkSymbol
                    || report.StartDate >= report.EndDate || index > 0 && report.StartDate != periods[index - 1].Report.EndDate)
                    throw new ArgumentException("需选择同一组合与基准的已完成单期报告，前一期结束日期须等于下一期开始日期。");
                if (details.Count is < 1 or > 200 || details.Any(d => string.IsNullOrWhiteSpace(d.SectorName) || d.SectorName.Length > 80 || d.SectorName.Any(char.IsControl)
                    || d.PortfolioWeight is < 0 or > 1 || d.BenchmarkWeight is < 0 or > 1 || d.PortfolioReturn is < -1 or > 1000000 || d.BenchmarkReturn is < -1 or > 1000000)
                    || details.Select(d => d.SectorName.Trim()).Distinct(StringComparer.OrdinalIgnoreCase).Count() != details.Count
                    || details.Sum(d => d.PortfolioWeight) != 1 || details.Sum(d => d.BenchmarkWeight) != 1)
                    throw new ArgumentException("单期行业权重或收益证据不完整，请重新核对原报告。");
                var calculated = details.Select(d => new AttributionDetail { SectorName = d.SectorName.Trim(), PortfolioWeight = d.PortfolioWeight,
                    BenchmarkWeight = d.BenchmarkWeight, PortfolioReturn = d.PortfolioReturn, BenchmarkReturn = d.BenchmarkReturn }).ToList();
                BrinsonModel.CalculateEffects(calculated);
                var portfolio = details.Sum(d => d.PortfolioWeight * d.PortfolioReturn);
                var benchmark = details.Sum(d => d.BenchmarkWeight * d.BenchmarkReturn);
                if (!Same(report.TotalActiveReturn, portfolio - benchmark) || !Same(report.AllocationEffect, calculated.Sum(d => d.Allocation))
                    || !Same(report.SelectionEffect, calculated.Sum(d => d.Selection)) || !Same(report.InteractionEffect, calculated.Sum(d => d.Interaction))
                    || details.Where((d, i) => !Same(d.Allocation, calculated[i].Allocation) || !Same(d.Selection, calculated[i].Selection) || !Same(d.Interaction, calculated[i].Interaction)).Any())
                    throw new ArgumentException("单期汇总与行业明细不一致，请重新核对原报告。");
                // Frongello: prior effects grow at this period's benchmark return,
                // while this period's effects are scaled by prior portfolio wealth.
                foreach (var key in sectors.Keys.ToArray())
                {
                    var previous = sectors[key];
                    sectors[key] = previous with { Allocation = previous.Allocation * (1 + benchmark), Selection = previous.Selection * (1 + benchmark), Interaction = previous.Interaction * (1 + benchmark) };
                }
                foreach (var detail in calculated)
                {
                    var previous = sectors.GetValueOrDefault(detail.SectorName) ?? new(detail.SectorName, 0, 0, 0);
                    sectors[detail.SectorName] = previous with { Allocation = previous.Allocation + detail.Allocation * portfolioGross,
                        Selection = previous.Selection + detail.Selection * portfolioGross, Interaction = previous.Interaction + detail.Interaction * portfolioGross };
                }
                portfolioGross *= 1 + portfolio; benchmarkGross *= 1 + benchmark;
                if (sectors.Count > 200 || portfolioGross > 1000001 || benchmarkGross > 1000001)
                    throw new ArgumentException("行业数量或累计收益超出多期归因支持范围。");
            }
            var rows = sectors.Values.OrderBy(s => s.SectorName, StringComparer.Ordinal).ToList();
            var allocation = rows.Sum(s => s.Allocation); var selection = rows.Sum(s => s.Selection); var interaction = rows.Sum(s => s.Interaction);
            var active = portfolioGross - benchmarkGross;
            var residual = active - allocation - selection - interaction;
            if (Math.Abs(residual) > .000000000001m) throw new ArgumentException("多期效应与累计超额收益不能对账。");
            return new(portfolioGross - 1, benchmarkGross - 1, active, allocation, selection, interaction, residual, rows);
        }
        catch (OverflowException) { throw new ArgumentException("多期收益超出精确计算支持范围。"); }
    }
}
