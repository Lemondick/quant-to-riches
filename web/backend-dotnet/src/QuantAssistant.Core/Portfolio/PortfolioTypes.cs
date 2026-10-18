namespace QuantAssistant.Core.Portfolio;

/// <summary>资产类型，与旧站点 entities.py 一致。</summary>
public enum AssetType
{
    Stock,
    Fund,
    Index,
    Cash,
}

/// <summary>交易动作，与旧站点 entities.py 一致。</summary>
public enum Action
{
    Buy,
    Add,
    Hold,
    Reduce,
    Sell,
    NoTrade,
}

public static class ActionSet
{
    public static bool IsBuy(Action a) => a is Action.Buy or Action.Add;
    public static bool IsSell(Action a) => a is Action.Reduce or Action.Sell;
    public static bool IsTransaction(Action a) => IsBuy(a) || IsSell(a);
}

/// <summary>成交输入（用户录入的精确值），对应 TransactionInput。</summary>
public sealed record TransactionInput(
    string Symbol,
    AssetType AssetType,
    Action Action,
    decimal Quantity,
    decimal Price,
    decimal Fees,
    DateTimeOffset TradedAt,
    string ExternalId,
    string Source = "manual",
    string? CashSymbol = null,
    string? Note = null);

/// <summary>分红输入（确认的现金分红，永不提交外部订单），对应 DividendInput。</summary>
public sealed record DividendInput(
    string Symbol,
    AssetType AssetType,
    decimal GrossAmount,
    decimal Fees,
    DateTimeOffset PaidAt,
    string ExternalId,
    string Source = "manual",
    string? CashSymbol = null,
    string? Note = null);

/// <summary>规范化并校验后的分红值。</summary>
public sealed record ValidatedDividend(
    string Symbol,
    AssetType AssetType,
    decimal GrossAmount,
    decimal Fees,
    decimal NetAmount,
    DateTimeOffset PaidAt,
    string Source,
    string ExternalId,
    string? CashSymbol,
    string? Note);

/// <summary>规范化并校验后的成交值（对应 _ValidatedTransaction）。</summary>
public sealed record ValidatedTransaction(
    string Symbol,
    AssetType AssetType,
    Action Action,
    decimal Quantity,
    decimal Price,
    decimal Fees,
    DateTimeOffset TradedAt,
    DateOnly TradeDate,
    string ExternalId,
    string Source,
    string? CashSymbol,
    string? Note,
    decimal GrossAmount,
    decimal CashDelta);

/// <summary>账本冲正输入（不可变反转请求），对应 LedgerCorrectionInput。</summary>
public sealed record LedgerCorrectionInput(
    string TargetType,
    long TargetId,
    string Reason,
    DateTimeOffset EffectiveAt,
    string ExternalId,
    string Source = "manual",
    string? Note = null);

/// <summary>资金流类型。</summary>
public enum CashFlowType
{
    Deposit,
    Withdrawal,
}

/// <summary>外部资金流输入（确认的入金/出金，带调用方幂等键），对应 ExternalCashFlowInput。</summary>
public sealed record ExternalCashFlowInput(
    string EventId,
    CashFlowType FlowType,
    decimal Amount,
    DateOnly EffectiveDate,
    string Reason,
    string? CashSymbol = null);

/// <summary>校准快照中的单个持仓，对应 PositionInput。</summary>
public sealed record CalibrationPosition(
    string Symbol,
    AssetType AssetType,
    decimal Quantity,
    decimal AverageCost,
    decimal MarketValue,
    DateOnly AsOfDate);

/// <summary>组合校准输入（完整对账快照 + 中性资金流），对应 PortfolioCalibrationInput。</summary>
public sealed record PortfolioCalibrationInput(
    string EventId,
    DateOnly AsOfDate,
    IReadOnlyList<CalibrationPosition> Positions,
    decimal NetExternalFlow,
    string Reason);

/// <summary>公司行动输入，对应 CorporateActionInput。</summary>
public sealed record CorporateActionInput(
    string ActionType,
    string Symbol,
    AssetType AssetType,
    DateTimeOffset EffectiveAt,
    string ExternalId,
    string Source = "manual",
    string? TargetSymbol = null,
    decimal? Quantity = null,
    decimal? TargetQuantity = null,
    decimal? Price = null,
    decimal? TargetPrice = null,
    decimal GrossAmount = 0m,
    decimal Fees = 0m,
    decimal? Ratio = null,
    string? CashSymbol = null,
    string? FromAccount = null,
    string? ToAccount = null,
    string? Note = null);
