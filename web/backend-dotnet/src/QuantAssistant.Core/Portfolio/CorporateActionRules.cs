namespace QuantAssistant.Core.Portfolio;

/// <summary>公司行动业务规则（纯函数），对应 record_corporate_action 的验证与数值计算。</summary>
public static class CorporateActionRules
{
    public static readonly string[] ActionTypes = ["dividend_reinvestment", "fund_conversion", "split", "custody_transfer"];

    public static bool IsValidActionType(string actionType) => ActionTypes.Contains(actionType);

    /// <summary>红利再投资数量 = gross_amount / price（归一化）。</summary>
    public static decimal ReinvestmentQuantity(decimal grossAmount, decimal price) => grossAmount / price;

    /// <summary>红利再投资成本 = (old_qty*old_cost + gross + fees) / new_qty。</summary>
    public static decimal ReinvestmentCost(decimal oldQuantity, decimal oldCost, decimal grossAmount, decimal fees, decimal newQuantity) =>
        (oldQuantity * oldCost + grossAmount + fees) / newQuantity;

    /// <summary>基金转换目标成本 = (target_old_qty*target_old_cost + old_cost*quantity) / new_target_qty。</summary>
    public static decimal ConversionTargetCost(decimal targetOldQuantity, decimal targetOldCost, decimal transferredCost, decimal newTargetQuantity) =>
        (targetOldQuantity * targetOldCost + transferredCost) / newTargetQuantity;

    /// <summary>拆分成本 = old_qty*old_cost / new_total。</summary>
    public static decimal SplitCost(decimal oldQuantity, decimal oldCost, decimal newTotal) => oldQuantity * oldCost / newTotal;
}
