using Action = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Core.Portfolio;

/// <summary>分红业务规则（对应 record_dividend 的验证）。</summary>
public static class DividendRules
{
    private static readonly TimeSpan ShanghaiOffset = TimeSpan.FromHours(8);

    public static ValidatedDividend Validate(DividendInput item)
    {
        var symbol = item.Symbol.Trim().ToUpperInvariant();
        if (string.IsNullOrEmpty(symbol))
        {
            throw new ArgumentException("标的代码不能为空");
        }

        if (item.AssetType is not (AssetType.Stock or AssetType.Fund))
        {
            throw new ArgumentException("现金分红只接受股票或基金");
        }

        if (item.GrossAmount <= 0 || item.Fees < 0 || item.Fees >= item.GrossAmount)
        {
            throw new ArgumentException("分红金额必须为正且费用必须小于分红金额");
        }

        if (item.PaidAt.Offset != ShanghaiOffset)
        {
            throw new ArgumentException("到账时间必须为上海时区时间");
        }

        var source = item.Source.Trim().ToLowerInvariant();
        var externalId = item.ExternalId.Trim();
        if (source.Length == 0 || externalId.Length == 0)
        {
            throw new ArgumentException("分红来源和编号不能为空");
        }

        var note = string.IsNullOrWhiteSpace(item.Note) ? null : item.Note.Trim();
        var cashSymbol = item.CashSymbol is null ? null : item.CashSymbol.Trim().ToUpperInvariant();
        return new ValidatedDividend(symbol, item.AssetType, item.GrossAmount, item.Fees, item.GrossAmount - item.Fees, item.PaidAt, source, externalId, cashSymbol, note);
    }
}
