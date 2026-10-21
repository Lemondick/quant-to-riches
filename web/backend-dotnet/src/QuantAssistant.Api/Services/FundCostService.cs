using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch3 流 A：持有成本后端。
/// GET api/v1/cost/positions：每只基金持仓成本/浮动盈亏/年化收益率/回本线。
/// 费率口径复用 C# FundFeeRules（唯一口径，不另写一套）；无已验证费率证据时赎回费与回本线留空并注明。
/// 税费仅文字提示，不做税务计算。
/// </summary>
public sealed class FundCostService(AccountDatabase database, FundFeeRepository fees)
{
    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    private static decimal Round4(decimal value) => decimal.Round(value, 4, MidpointRounding.AwayFromZero);

    public const string TaxNote = "税费提示（仅文字提示，不做税务计算）：基金份额赎回涉及的印花税/所得税" +
        "口径以税务机关最新政策为准；本页仅展示持有成本测算，不计算、不代扣任何税费，不构成税务建议。";

    private static readonly string PositionsSql = """
        SELECT p.symbol, COALESCE(i.name, p.symbol) AS name, p.quantity::text AS quantity,
               p.average_cost::text AS average_cost, p.market_value::text AS market_value
        FROM (SELECT user_id, symbol, quantity, average_cost, market_value FROM positions WHERE user_id=$1 AND asset_type='fund' AND quantity>0
              UNION
              SELECT p.user_id, p.symbol, p.quantity, p.average_cost, p.market_value
              FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id
              WHERE p.user_id=$1 AND a.status='active' AND p.asset_type='fund' AND p.quantity>0) p
        LEFT JOIN instruments i ON i.user_id=p.user_id AND i.symbol=p.symbol
        ORDER BY p.symbol
        """;

    private static readonly string LotsSql = """
        SELECT l.confirmed_on::text AS confirmed_on, l.remaining_units::text AS units
        FROM fund_holding_lots l
        WHERE l.user_id=$1 AND l.snapshot_id=(SELECT s.id FROM fund_lot_snapshots s WHERE s.user_id=$1 AND s.symbol=$2 ORDER BY s.id DESC LIMIT 1)
          AND l.remaining_units > 0
        """;

    public JsonObject Positions(string userId) => database.Read(sql =>
    {
        var today = FundFeeRepository.Today();
        var items = new JsonArray();
        foreach (var row in sql.Query(PositionsSql, userId).OfType<JsonObject>())
        {
            var symbol = row["symbol"]!.GetValue<string>();
            var quantity = decimal.Parse(row["quantity"]!.GetValue<string>(), Inv);
            var averageCost = decimal.Parse(row["average_cost"]!.GetValue<string>(), Inv);
            var marketValue = decimal.Parse(row["market_value"]!.GetValue<string>(), Inv);
            var totalCost = FundFeeRules.Round(averageCost * quantity);
            var pnl = FundFeeRules.Round(marketValue - totalCost);
            decimal? pnlRate = totalCost == 0 ? null : Round4(pnl / totalCost);
            // 加权持有天数：按批次剩余份额加权（批次口径见 §8）。
            double unitsSum = 0, dayUnits = 0;
            foreach (var lot in sql.Query(LotsSql, userId, symbol).OfType<JsonObject>())
            {
                var units = (double)decimal.Parse(lot["units"]!.GetValue<string>(), Inv);
                var days = Math.Max(0, today.DayNumber - DateOnly.Parse(lot["confirmed_on"]!.GetValue<string>(), Inv).DayNumber);
                unitsSum += units; dayUnits += units * days;
            }
            double? weightedDays = unitsSum > 0 ? dayUnits / unitsSum : null;
            // 年化：几何年化 (1+浮动盈亏率)^(365/加权持有天数)-1。
            decimal? annualized = null;
            var basis = "成本=positions.average_cost×quantity；年化=(1+浮动盈亏率)^(365/加权持有天数)-1（几何年化）";
            if (weightedDays is null or < 1) basis += "；无有效批次持有天数，年化未计";
            else if (pnlRate is { } rate && 1 + rate > 0)
                annualized = Round4((decimal)(Math.Pow((double)(1 + rate), 365.0 / weightedDays.Value) - 1));
            else basis += "；浮动盈亏率≤-100%，年化无意义未计";
            // 赎回费率：FundFeeRules 唯一口径（不另写一套）。
            decimal? redemptionRate = null, redemptionFee = null, breakEven = null;
            string feeBasis;
            try
            {
                var schedule = fees.Current(sql, userId, symbol, today);
                redemptionRate = FundFeeRules.RedemptionRate(schedule, (int)Math.Floor(weightedDays ?? 0));
                redemptionFee = FundFeeRules.Round(marketValue * redemptionRate.Value);
                if (redemptionRate.Value < 1 && averageCost > 0)
                    breakEven = Round4(averageCost / (1 - redemptionRate.Value)); // 回本线：扣赎回费后到手=成本所需的单位净值
                feeBasis = $"FundFeeRules（唯一口径）；verified_on={schedule.VerifiedOn:yyyy-MM-dd}；evidence={schedule.EvidenceReference}";
            }
            catch (Exception error) when (error is UnverifiedFeeScheduleError or ArgumentException)
            {
                feeBasis = "fee_schedule_missing：未找到已验证费率证据，赎回费与回本线未计";
            }
            items.Add(new JsonObject
            {
                ["symbol"] = symbol, ["name"] = row["name"]?.GetValue<string>(),
                ["quantity"] = JsonValue.Create(quantity), ["average_cost"] = JsonValue.Create(averageCost),
                ["total_cost"] = JsonValue.Create(totalCost), ["market_value"] = JsonValue.Create(marketValue),
                ["floating_pnl"] = JsonValue.Create(pnl), ["pnl_rate"] = pnlRate is null ? null : JsonValue.Create(pnlRate.Value),
                ["weighted_holding_days"] = weightedDays is null ? null : JsonValue.Create(Math.Round(weightedDays.Value, 1)),
                ["annualized_return"] = annualized is null ? null : JsonValue.Create(annualized.Value),
                ["redemption_rate"] = redemptionRate is null ? null : JsonValue.Create(redemptionRate.Value),
                ["redemption_fee_estimate"] = redemptionFee is null ? null : JsonValue.Create(redemptionFee.Value),
                ["break_even_nav"] = breakEven is null ? null : JsonValue.Create(breakEven.Value),
                ["cost_basis"] = basis, ["fee_basis"] = feeBasis,
            });
        }
        return new JsonObject
        {
            ["items"] = items, ["count"] = items.Count,
            ["fee_caliber"] = "FundFeeRules（C# 唯一口径，与赎回试算一致）",
            ["tax_note"] = TaxNote, ["research_only"] = true,
        };
    });
}
