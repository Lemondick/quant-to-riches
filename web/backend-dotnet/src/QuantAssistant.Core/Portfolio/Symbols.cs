using System.Text.RegularExpressions;

namespace QuantAssistant.Core.Portfolio;

/// <summary>标的代码规范化（对应 Python common/symbols.py canonical_symbol）。</summary>
public static partial class Symbols
{
    [GeneratedRegex(@"^(\d{6})(?:\.(SH|SZ|BJ))?$")]
    private static partial Regex ASharePattern();

    /// <summary>规范化为交易所限定代码：非股票原样大写；A 股六位码按前缀推断交易所。</summary>
    public static string Canonical(string value, AssetType assetType, string? exchange = null)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException("标的代码不能为空");
        }

        var text = value.Trim().ToUpperInvariant();
        if (assetType != AssetType.Stock)
        {
            return text;
        }

        var match = ASharePattern().Match(text);
        if (!match.Success)
        {
            return text;
        }

        var code = match.Groups[1].Value;
        var suffix = match.Groups[2].Success ? match.Groups[2].Value : (exchange is "SH" or "SZ" or "BJ" ? exchange : InferExchange(code));
        return $"{code}.{suffix}";
    }

    /// <summary>按六位码前缀推断交易所（4/8/92→BJ，6/9→SH，0/2/3→SZ）。</summary>
    public static string InferExchange(string code)
    {
        if (code.StartsWith("4") || code.StartsWith("8") || code.StartsWith("92"))
        {
            return "BJ";
        }

        if (code.StartsWith("6") || code.StartsWith("9"))
        {
            return "SH";
        }

        if (code.StartsWith("0") || code.StartsWith("2") || code.StartsWith("3"))
        {
            return "SZ";
        }

        return "BJ";
    }
}
