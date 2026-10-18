using QuantAssistant.Core.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>标的代码规范化（对应 Python canonical_symbol）单元测试。</summary>
public sealed class SymbolsTests
{
    [Theory]
    [InlineData("600000", "600000.SH")]
    [InlineData("688001", "688001.SH")]
    [InlineData("000001", "000001.SZ")]
    [InlineData("300750", "300750.SZ")]
    [InlineData("430047", "430047.BJ")]
    [InlineData("830799", "830799.BJ")]
    public void Stock_InfersExchange(string code, string expected)
    {
        Assert.Equal(expected, Symbols.Canonical(code, AssetType.Stock));
    }

    [Theory]
    [InlineData("600000.SZ", "600000.SZ")] // 显式后缀优先
    [InlineData("600000.sh", "600000.SH")] // 小写后缀规范化
    public void Stock_ExplicitSuffixWins(string code, string expected)
    {
        Assert.Equal(expected, Symbols.Canonical(code, AssetType.Stock));
    }

    [Theory]
    [InlineData("510300", "510300")] // 基金不推断交易所
    [InlineData("us_aapl", "US_AAPL")] // 非 A 股原样大写
    public void NonStock_Unchanged(string code, string expected)
    {
        Assert.Equal(expected, Symbols.Canonical(code, AssetType.Fund));
    }
}
