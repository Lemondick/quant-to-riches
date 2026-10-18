using QuantAssistant.Core.Portfolio;
using Action = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Core.Abstractions;

/// <summary>现金持仓状态（positions 表 asset_type='cash' 的特殊行）。</summary>
public sealed record CashState(string Symbol, decimal Balance, DateOnly AsOfDate);

/// <summary>证券持仓状态。</summary>
public sealed record PositionState(string Symbol, AssetType AssetType, decimal Quantity, decimal AverageCost, decimal MarketValue, DateOnly AsOfDate, string? Name = null);

/// <summary>已入库成交行（用于幂等校验）。</summary>
public sealed record LedgerTransaction(
    long Id, string Symbol, AssetType AssetType, Action Action,
    decimal Quantity, decimal Price, decimal Fees, DateTimeOffset TradedAt,
    string? CashSymbol, string? Note);

public sealed record LedgerEntry(
    long Id, string EntryType, string Symbol, string Name, AssetType AssetType,
    decimal? Quantity, decimal? Price, decimal GrossAmount, decimal Fees, decimal NetAmount,
    DateTimeOffset OccurredAt, string ExternalId, string? Note, bool Reversed);

public sealed record LedgerSnapshot(IReadOnlyList<LedgerEntry> Entries, decimal TotalFees, decimal TotalDividends);

public sealed record RecordedLedgerRequest(string RecordJson, string? Fingerprint);

/// <summary>
/// 账本仓储契约（P3）：成交、持仓、现金、审计。实现见 Infrastructure.Portfolio.LedgerRepository。
/// 所有方法均按 user_id 隔离（计划书 §3「所有查询按用户隔离」）。
/// </summary>
public interface ILedgerRepository
{
    T ExecuteAtomically<T>(string userId, Func<ILedgerRepository, T> operation);
    LedgerSnapshot ReadLedger(string userId, int limit);
    Task<LedgerSnapshot> ReadLedgerAsync(string userId, int limit, CancellationToken cancellation = default);
    LedgerEntry? FindLedgerEntry(string userId, long id, bool dividend);
    void EnsureInstrument(string userId, string symbol, AssetType assetType, string name, DateOnly asOfDate);
    LedgerTransaction? FindTransaction(string userId, string source, string externalId);
    DateOnly? LatestValuationDate(string userId);
    bool HasConfirmedBaseline(string userId);
    void RequireKnownInstrument(string userId, string symbol, AssetType assetType);
    CashState GetCashState(string userId, string? cashSymbol);
    PositionState? GetPositionState(string userId, string symbol);
    long InsertTransaction(string userId, ValidatedTransaction v, string cashSymbol, DateTime recordedAt);
    void UpsertPosition(string userId, string symbol, AssetType assetType, decimal quantity, decimal averageCost, decimal marketValue, DateOnly asOfDate, DateTime updatedAt);
    void DeletePosition(string userId, string symbol);
    void WriteCashPosition(string userId, string cashSymbol, decimal balance, DateOnly asOfDate, DateTime updatedAt);
    void InsertAudit(string userId, string entityType, string entityId, DateTime changedAt, string? beforeJson, string afterJson);

    // 分红
    long? FindDividend(string userId, string source, string externalId);
    long InsertDividend(string userId, ValidatedDividend v, string cashSymbol, DateTime recordedAt);

    // 冲正
    long? FindCorrection(string userId, string source, string externalId);
    CorrectionTarget? GetTransactionForCorrection(string userId, long targetId);
    DividendTarget? GetDividendForCorrection(string userId, long targetId);
    string? GetAuditBeforeJson(string userId, string entityType, string entityId);
    RecordedLedgerRequest? ReadRecordedRequest(string userId, string entityType, string entityId);
    bool HasLaterUnreversedSell(string userId, string symbol, DateTimeOffset tradedAt, long targetId);
    bool HasCorrectionDependencies(string userId, string symbol, DateTimeOffset tradedAt);
    long InsertCorrection(string userId, string targetType, long targetId, string reason, DateTimeOffset effectiveAt, DateTime recordedAt, string source, string externalId, string? note);
    void MarkReversed(string userId, string targetType, long targetId, long correctionId);

    // 公司行动
    long? FindCorporateAction(string userId, string source, string externalId);
    long InsertCorporateAction(string userId, CorporateActionRecord record);

    // 外部资金流
    PortfolioEvent? FindPortfolioEvent(string userId, string eventId);
    void InsertCashFlowEvent(string userId, string eventId, string flowType, DateOnly effectiveDate, decimal signedAmount, string reason, string detailsJson, DateTime recordedAt);

    // 组合校准
    IReadOnlyList<PositionState> ListPositions(string userId);
    Task<IReadOnlyList<PositionState>> ListPositionsAsync(string userId, CancellationToken cancellation = default);
    void ReplaceAllPositions(string userId, IReadOnlyList<CalibrationPosition> positions, DateOnly asOfDate, DateTime updatedAt);
    void InsertCalibrationEvent(string userId, string eventId, DateOnly asOfDate, decimal netExternalFlow, string reason, string detailsJson, DateTime recordedAt);
}

/// <summary>组合事件（外部资金流/校准等）。</summary>
public sealed record PortfolioEvent(string EventId, string EventType, DateOnly EffectiveDate, decimal Amount, string Status, string Reason, string? DetailsJson);

/// <summary>公司行动入库记录。</summary>
public sealed record CorporateActionRecord(
    string ActionType, string Symbol, string? TargetSymbol, AssetType AssetType,
    decimal? Quantity, decimal? TargetQuantity, decimal? Price, decimal? TargetPrice,
    decimal GrossAmount, decimal Fees, decimal CashDelta, decimal? Ratio,
    DateTimeOffset EffectiveAt, string Source, string ExternalId, string? CashSymbol,
    string? FromAccount, string? ToAccount, string? Note);

/// <summary>待冲正的成交行。</summary>
public sealed record CorrectionTarget(
    long Id, string Symbol, AssetType AssetType, Action Action,
    decimal Quantity, decimal Price, decimal Fees, decimal CashDelta,
    string? CashSymbol, DateTimeOffset TradedAt, bool AlreadyReversed);

/// <summary>待冲正的分红行。</summary>
public sealed record DividendTarget(
    long Id, decimal NetAmount, string? CashSymbol, DateTimeOffset PaidAt, bool AlreadyReversed);
