using QuantAssistant.Core.Portfolio;
using Xunit;
using Action = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Api.Tests;

public sealed class TransactionRulesTests
{
    private static readonly TimeSpan Shanghai = TimeSpan.FromHours(8);

    private static TransactionInput Input(string symbol, AssetType type, Action action, decimal qty, decimal price, decimal fees) =>
        new(symbol, type, action, qty, price, fees,
            new DateTimeOffset(2026, 10, 8, 10, 0, 0, Shanghai), "EXT-001");

    [Fact]
    public void Buy_GrossAmountAndCashDelta()
    {
        var v = TransactionRules.Validate(Input("510300", AssetType.Fund, Action.Buy, 200m, 10.5m, 5m));
        Assert.Equal(2100m, v.GrossAmount);
        Assert.Equal(-2105m, v.CashDelta);
    }

    [Fact]
    public void Sell_GrossAmountAndCashDelta()
    {
        var v = TransactionRules.Validate(Input("510300", AssetType.Fund, Action.Sell, 100m, 12m, 2m));
        Assert.Equal(1200m, v.GrossAmount);
        Assert.Equal(1198m, v.CashDelta);
    }

    [Fact]
    public void BuyAverageCost_IncludesFees()
    {
        // (0*0 + 200*10.5 + 5) / 200 = 2105/200
        var cost = TransactionRules.BuyAverageCost(0m, 0m, 200m, 10.5m, 5m);
        Assert.Equal(10.525m, cost);
    }

    [Fact]
    public void BuyAverageCost_AfterExistingPosition()
    {
        // (200*10.525 + 100*11 + 3) / 300 = (2105 + 1100 + 3)/300 = 3208/300
        var cost = TransactionRules.BuyAverageCost(200m, 10.525m, 100m, 11m, 3m);
        Assert.Equal(3208m / 300m, cost);
    }

    [Fact]
    public void Sell_KeepsAverageCost()
    {
        Assert.Equal(10.525m, TransactionRules.SellAverageCost(10.525m));
    }

    [Fact]
    public void Stock_BuyRequiresLotsOf100()
    {
        Assert.Throws<ArgumentException>(() =>
            TransactionRules.Validate(Input("600000", AssetType.Stock, Action.Buy, 150m, 10m, 1m)));
    }

    [Fact]
    public void Stock_RequiresIntegerQuantity()
    {
        Assert.Throws<ArgumentException>(() =>
            TransactionRules.Validate(Input("600000", AssetType.Stock, Action.Buy, 100.5m, 10m, 1m)));
    }

    [Fact]
    public void Sell_FeesCannotExceedProceeds()
    {
        Assert.Throws<ArgumentException>(() =>
            TransactionRules.Validate(Input("510300", AssetType.Fund, Action.Sell, 10m, 1m, 15m)));
    }

    [Fact]
    public void BuyAndAdd_AreBuyActions()
    {
        Assert.True(ActionSet.IsBuy(Action.Buy));
        Assert.True(ActionSet.IsBuy(Action.Add));
        Assert.True(ActionSet.IsSell(Action.Reduce));
        Assert.True(ActionSet.IsSell(Action.Sell));
        Assert.False(ActionSet.IsTransaction(Action.Hold));
    }
}
