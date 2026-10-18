using QuantAssistant.Core.Errors;
using Action = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Core.Portfolio;

/// <summary>
/// 成交业务规则（纯函数，对应 transactions_commands.py 的 _validate_transaction_input 与成本计算）。
/// 数值规则必须与旧站点逐位一致（计划书 §3「账本和数值」、P3「逐事件比较差异为零」）。
/// </summary>
public static class TransactionRules
{
    /// <summary>上海时区固定偏移（旧站点 SHANGHAI_TZ 为 +08:00）。</summary>
    private static readonly TimeSpan ShanghaiOffset = TimeSpan.FromHours(8);

    /// <summary>规范化并校验成交输入，返回可入库的规范值；非法输入抛 LedgerStateError/ArgumentException。</summary>
    public static ValidatedTransaction Validate(TransactionInput item)
    {
        var symbol = item.Symbol.Trim().ToUpperInvariant();
        if (string.IsNullOrEmpty(symbol))
        {
            throw new ArgumentException("标的代码不能为空");
        }

        if (item.AssetType is not (AssetType.Stock or AssetType.Fund))
        {
            throw new ArgumentException("实际成交只接受股票或基金");
        }

        if (!ActionSet.IsTransaction(item.Action))
        {
            throw new ArgumentException("交易记录只接受BUY、ADD、REDUCE或SELL");
        }

        if (item.Quantity <= 0)
        {
            throw new ArgumentException("交易数量必须大于0");
        }

        if (item.Price <= 0)
        {
            throw new ArgumentException("交易价格必须大于0");
        }

        if (item.Fees < 0)
        {
            throw new ArgumentException("手续费不能为负");
        }

        // 交易时间必须为上海时区（旧站点 _require_shanghai_datetime）。
        var tradedAt = item.TradedAt;
        if (tradedAt.Offset != ShanghaiOffset)
        {
            throw new ArgumentException("交易时间必须为上海时区时间");
        }

        var externalId = item.ExternalId.Trim();
        var source = item.Source.Trim().ToLowerInvariant();
        if (externalId.Length == 0)
        {
            throw new ArgumentException("成交编号不能为空");
        }

        if (source.Length == 0)
        {
            throw new ArgumentException("成交来源不能为空");
        }

        var note = string.IsNullOrWhiteSpace(item.Note) ? null : item.Note.Trim();
        var cashSymbol = item.CashSymbol is null ? null : item.CashSymbol.Trim().ToUpperInvariant();

        // 股票整股约束（旧站点：整股 + 买入 100 股整数倍）。
        if (item.AssetType == AssetType.Stock)
        {
            if (decimal.Truncate(item.Quantity) != item.Quantity)
            {
                throw new ArgumentException("股票交易数量必须是整股");
            }

            if (ActionSet.IsBuy(item.Action) && item.Quantity % 100m != 0)
            {
                throw new ArgumentException("股票买入/加仓数量必须是100股的整数倍");
            }
        }

        var grossAmount = item.Quantity * item.Price;
        var cashDelta = ActionSet.IsBuy(item.Action) ? -(grossAmount + item.Fees) : grossAmount - item.Fees;
        if (ActionSet.IsSell(item.Action) && cashDelta < 0)
        {
            throw new ArgumentException("卖出手续费不能超过成交金额");
        }

        return new ValidatedTransaction(
            symbol, item.AssetType, item.Action, item.Quantity, item.Price, item.Fees,
            tradedAt, DateOnly.FromDateTime(tradedAt.UtcDateTime.Add(ShanghaiOffset)),
            externalId, source, cashSymbol, note, grossAmount, cashDelta);
    }

    /// <summary>买入加权平均成本：new = (old_qty*old_cost + qty*price + fees) / new_qty（fees 计入成本）。</summary>
    public static decimal BuyAverageCost(decimal oldQuantity, decimal oldAverageCost, decimal quantity, decimal price, decimal fees)
    {
        var newQuantity = oldQuantity + quantity;
        return (oldQuantity * oldAverageCost + quantity * price + fees) / newQuantity;
    }

    /// <summary>卖出后成本保持不变（旧站点「卖出前成本」语义）。</summary>
    public static decimal SellAverageCost(decimal oldAverageCost) => oldAverageCost;
}
