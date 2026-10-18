namespace QuantAssistant.Core.Portfolio;

/// <summary>冲正业务规则（纯函数），对应 correct_ledger_entry 的成本重算。</summary>
public static class CorrectionRules
{
    /// <summary>买入冲正成本：new = (current_cost*current_qty - qty*price - fees) / new_qty。</summary>
    public static decimal BuyCorrectionAverageCost(decimal currentQuantity, decimal currentAverageCost, decimal quantity, decimal price, decimal fees)
    {
        var newQuantity = currentQuantity - quantity;
        if (newQuantity == 0)
        {
            return 0m;
        }

        var newBasis = currentAverageCost * currentQuantity - (quantity * price + fees);
        return newBasis / newQuantity;
    }

    /// <summary>卖出冲正成本：new = (current_cost*current_qty + qty*original_cost) / new_qty。</summary>
    public static decimal SellCorrectionAverageCost(decimal currentQuantity, decimal currentAverageCost, decimal quantity, decimal originalAverageCost)
    {
        var newQuantity = currentQuantity + quantity;
        var oldBasis = currentAverageCost * currentQuantity;
        return (oldBasis + quantity * originalAverageCost) / newQuantity;
    }
}
