using System.Text.Json;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using Action = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Core.Portfolio;

/// <summary>
/// 成交记录事务编排（对应 Python _PortfolioCommandTransactions.record_transaction）。
/// 验证 → 幂等 → 约束检查 → 插入 → 更新持仓/现金 → 审计，逻辑与旧站点逐条一致（P3「逐事件比较差异为零」）。
/// </summary>
public sealed class TransactionService(ILedgerRepository repository, TimeProvider timeProvider)
{
    private static readonly TimeSpan ShanghaiOffset = TimeSpan.FromHours(8);

    /// <summary>记录一笔成交，返回成交 ID；幂等（同 source+external_id 返回原 ID）。</summary>
    public long RecordTransaction(string userId, TransactionInput item, string? instrumentName = null) =>
        repository.ExecuteAtomically(userId, scoped => new TransactionService(scoped, timeProvider).RecordTransactionCore(userId, item, instrumentName));

    private long RecordTransactionCore(string userId, TransactionInput item, string? instrumentName)
    {
        var v = TransactionRules.Validate(item);
        if (instrumentName is not null && (string.IsNullOrWhiteSpace(instrumentName) || instrumentName.Trim().Length > 80))
            throw new ArgumentException("标的名称须为1至80个字符");
        var recordedAt = NowUtc();
        if (v.TradedAt > recordedAt)
        {
            throw new ArgumentException("交易时间不能晚于记录时间");
        }

        // 幂等：相同来源+编号返回原 ID，内容不同报冲突。
        var existing = repository.FindTransaction(userId, v.Source, v.ExternalId);
        if (existing is not null)
        {
            LedgerIdempotency.RequireSame(repository.ReadRecordedRequest(userId, "transaction", existing.Id.ToString()),
                LedgerRequests.Transaction(v, v.CashSymbol ?? existing.CashSymbol));
            return existing.Id;
        }

        // 估值日约束 + 净值基线约束。
        var latestValuation = repository.LatestValuationDate(userId);
        if (latestValuation is not null && v.TradeDate <= latestValuation)
        {
            throw new ArgumentException("成交日期不得早于或等于最近已确认估值日");
        }

        if (latestValuation is null && repository.HasConfirmedBaseline(userId))
        {
            throw new LedgerStateError(ErrorCodes.NavBaselinePending);
        }

        if (instrumentName is not null)
            repository.EnsureInstrument(userId, v.Symbol, v.AssetType, instrumentName.Trim(), v.TradeDate);
        repository.RequireKnownInstrument(userId, v.Symbol, v.AssetType);

        var cash = repository.GetCashState(userId, v.CashSymbol);
        if (cash.AsOfDate > v.TradeDate)
        {
            throw new ArgumentException("交易时间早于当前现金账本日期，无法重算现金");
        }

        var newCash = cash.Balance + v.CashDelta;
        if (newCash < 0)
        {
            throw new LedgerStateError(ErrorCodes.InsufficientCash);
        }

        var current = repository.GetPositionState(userId, v.Symbol);
        if (current is not null && current.AsOfDate > v.TradeDate)
        {
            throw new ArgumentException("交易时间早于当前持仓日期，无法重算持仓");
        }

        var oldQuantity = current?.Quantity ?? 0m;
        var oldAverageCost = current?.AverageCost ?? 0m;
        if (ActionSet.IsSell(v.Action) && v.Quantity > oldQuantity)
        {
            throw new LedgerStateError(ErrorCodes.InsufficientPosition);
        }

        var transactionId = repository.InsertTransaction(userId, v, cash.Symbol, recordedAt);

        decimal newQuantity;
        decimal newAverageCost;
        if (ActionSet.IsBuy(v.Action))
        {
            newQuantity = oldQuantity + v.Quantity;
            newAverageCost = TransactionRules.BuyAverageCost(oldQuantity, oldAverageCost, v.Quantity, v.Price, v.Fees);
        }
        else
        {
            newQuantity = oldQuantity - v.Quantity;
            newAverageCost = TransactionRules.SellAverageCost(oldAverageCost);
        }

        if (newQuantity == 0)
        {
            repository.DeletePosition(userId, v.Symbol);
        }
        else
        {
            repository.UpsertPosition(userId, v.Symbol, v.AssetType, newQuantity, newAverageCost, newQuantity * v.Price, v.TradeDate, recordedAt);
        }

        repository.WriteCashPosition(userId, cash.Symbol, newCash, v.TradeDate, recordedAt);

        repository.InsertAudit(userId, "transaction", transactionId.ToString(), recordedAt, BeforeJson(current),
            LedgerIdempotency.WithRequest(AfterJson(v, cash, newCash, recordedAt), LedgerRequests.Transaction(v, cash.Symbol)));
        return transactionId;
    }

    /// <summary>记录一笔现金分红，返回分红 ID；幂等（同 source+external_id 返回原 ID）。</summary>
    public long RecordDividend(string userId, DividendInput item) =>
        repository.ExecuteAtomically(userId, scoped => new TransactionService(scoped, timeProvider).RecordDividendCore(userId, item));

    private long RecordDividendCore(string userId, DividendInput item)
    {
        var v = DividendRules.Validate(item);
        var recordedAt = NowUtc();
        if (v.PaidAt > recordedAt)
        {
            throw new ArgumentException("分红到账时间不能晚于记录时间");
        }

        var existing = repository.FindDividend(userId, v.Source, v.ExternalId);
        if (existing is not null)
        {
            var request = repository.ReadRecordedRequest(userId, "dividend", existing.Value.ToString());
            LedgerIdempotency.RequireSame(request, LedgerRequests.Dividend(v, v.CashSymbol ?? LedgerIdempotency.RecordedCash(request)));
            return existing.Value;
        }

        var paidDate = DateOnly.FromDateTime(v.PaidAt.UtcDateTime.Add(ShanghaiOffset));
        var latestValuation = repository.LatestValuationDate(userId);
        if (latestValuation is not null && paidDate <= latestValuation)
            throw new ApplicationFailure("dividend_date_invalid", 422, "分红到账日期不得早于或等于最近已确认估值日，请使用冲正或历史重算流程。");

        repository.RequireKnownInstrument(userId, v.Symbol, v.AssetType);
        var cash = repository.GetCashState(userId, v.CashSymbol);
        if (cash.AsOfDate > DateOnly.FromDateTime(v.PaidAt.UtcDateTime.Add(ShanghaiOffset)))
        {
            throw new ArgumentException("分红时间早于当前现金账本日期，无法重算现金");
        }

        var dividendId = repository.InsertDividend(userId, v, cash.Symbol, recordedAt);
        repository.WriteCashPosition(userId, cash.Symbol, cash.Balance + v.NetAmount, DateOnly.FromDateTime(v.PaidAt.UtcDateTime.Add(ShanghaiOffset)), recordedAt);
        repository.InsertAudit(userId, "dividend", dividendId.ToString(), recordedAt, null, LedgerIdempotency.WithRequest(JsonSerializer.Serialize(new
        {
            symbol = v.Symbol,
            asset_type = v.AssetType.ToString().ToLowerInvariant(),
            gross_amount = v.GrossAmount,
            fees = v.Fees,
            net_amount = v.NetAmount,
            paid_at = v.PaidAt,
            source = v.Source,
            external_id = v.ExternalId,
        }), LedgerRequests.Dividend(v, cash.Symbol)));
        return dividendId;
    }

    /// <summary>冲正一笔成交/分红（不改动原行，反向更新持仓/现金并标记），返回冲正 ID；幂等。</summary>
    public long CorrectLedgerEntry(string userId, LedgerCorrectionInput item) =>
        repository.ExecuteAtomically(userId, scoped => new TransactionService(scoped, timeProvider).CorrectLedgerEntryCore(userId, item));

    private long CorrectLedgerEntryCore(string userId, LedgerCorrectionInput item)
    {
        var targetType = item.TargetType.Trim().ToLowerInvariant();
        if (targetType is not ("transaction" or "dividend"))
        {
            throw new ArgumentException("冲正目标必须是transaction或dividend");
        }

        if (item.TargetId <= 0)
        {
            throw new ArgumentException("冲正目标编号无效");
        }

        if (string.IsNullOrWhiteSpace(item.Reason))
        {
            throw new ArgumentException("冲正原因不能为空");
        }

        if (item.EffectiveAt.Offset != TimeSpan.FromHours(8))
        {
            throw new ArgumentException("冲正时间必须为上海时区时间");
        }

        var recordedAt = NowUtc();
        if (item.EffectiveAt > recordedAt)
        {
            throw new ArgumentException("冲正时间不能晚于记录时间");
        }

        var source = item.Source.Trim().ToLowerInvariant();
        var externalId = item.ExternalId.Trim();
        if (source.Length == 0 || externalId.Length == 0)
        {
            throw new ArgumentException("冲正来源和编号不能为空");
        }

        var note = string.IsNullOrWhiteSpace(item.Note) ? null : item.Note.Trim();

        // 幂等
        var duplicate = repository.FindCorrection(userId, source, externalId);
        if (duplicate is not null)
        {
            LedgerIdempotency.RequireSame(repository.ReadRecordedRequest(userId, "ledger_correction", duplicate.Value.ToString()), LedgerRequests.Correction(item));
            return duplicate.Value;
        }

        var latest = repository.LatestValuationDate(userId);
        if (latest is not null && DateOnly.FromDateTime(item.EffectiveAt.UtcDateTime.AddHours(8)) <= latest)
        {
            throw new ArgumentException("冲正日期不得早于或等于最近已确认估值日");
        }

        long correctionId;
        if (targetType == "transaction")
        {
            correctionId = CorrectTransaction(userId, item, recordedAt, source, externalId, note);
        }
        else
        {
            correctionId = CorrectDividend(userId, item, recordedAt, source, externalId, note);
        }

        repository.InsertAudit(userId, "ledger_correction", correctionId.ToString(), recordedAt, null, LedgerIdempotency.WithRequest(JsonSerializer.Serialize(new
        {
            target_type = targetType,
            target_id = item.TargetId,
            reason = item.Reason,
        }), LedgerRequests.Correction(item)));
        return correctionId;
    }

    private long CorrectTransaction(string userId, LedgerCorrectionInput item, DateTime recordedAt, string source, string externalId, string? note)
    {
        var row = repository.GetTransactionForCorrection(userId, item.TargetId)
            ?? throw new TransactionNotFoundError();
        if (row.AlreadyReversed)
        {
            throw new ArgumentException("该成交已经冲正");
        }

        var position = repository.GetPositionState(userId, row.Symbol);
        var cash = repository.GetCashState(userId, row.CashSymbol);
        var effectiveDate = DateOnly.FromDateTime(item.EffectiveAt.UtcDateTime.AddHours(8));

        if (item.EffectiveAt < row.TradedAt || cash.AsOfDate > effectiveDate || position?.AsOfDate > effectiveDate)
        {
            throw new LedgerStateError(ErrorCodes.LedgerCorrectionDateInvalid);
        }

        if (repository.HasCorrectionDependencies(userId, row.Symbol, row.TradedAt))
            throw new LedgerStateError(ErrorCodes.LedgerCorrectionDependentEvents);

        var buy = ActionSet.IsBuy(row.Action);
        var currentQuantity = position?.Quantity ?? 0m;

        if (buy && repository.HasLaterUnreversedSell(userId, row.Symbol, row.TradedAt, item.TargetId))
        {
            throw new LedgerStateError(ErrorCodes.LedgerCorrectionDependentEvents);
        }

        var newQuantity = buy ? currentQuantity - row.Quantity : currentQuantity + row.Quantity;
        if (newQuantity < 0)
        {
            throw new ArgumentException("冲正将导致持仓为负");
        }

        decimal newAverage;
        if (buy)
        {
            if (position is not null && currentQuantity > 0)
            {
                var newBasis = position.AverageCost * currentQuantity - (row.Quantity * row.Price + row.Fees);
                if (newQuantity > 0 && newBasis < 0)
                {
                    throw new LedgerStateError(ErrorCodes.LedgerCorrectionDependentEvents);
                }

                newAverage = CorrectionRules.BuyCorrectionAverageCost(currentQuantity, position.AverageCost, row.Quantity, row.Price, row.Fees);
            }
            else
            {
                newAverage = 0m;
            }
        }
        else
        {
            // 卖出冲正需要「成交前成本」快照，否则不能用卖出价替代。
            var beforeJson = repository.GetAuditBeforeJson(userId, "transaction", item.TargetId.ToString());
            var originalAverage = ExtractOriginalAverage(beforeJson);
            if (originalAverage is null)
            {
                throw new LedgerStateError(ErrorCodes.LedgerCorrectionCostMissing);
            }

            newAverage = CorrectionRules.SellCorrectionAverageCost(currentQuantity, position?.AverageCost ?? 0m, row.Quantity, originalAverage.Value);
        }

        var unitValue = position is not null && currentQuantity > 0 ? position.MarketValue / currentQuantity : row.Price;
        if (newQuantity == 0)
        {
            repository.DeletePosition(userId, row.Symbol);
        }
        else
        {
            repository.UpsertPosition(userId, row.Symbol, row.AssetType, newQuantity, newAverage, newQuantity * unitValue, effectiveDate, recordedAt);
        }

        repository.WriteCashPosition(userId, cash.Symbol, cash.Balance - row.CashDelta, effectiveDate, recordedAt);

        var correctionId = repository.InsertCorrection(userId, "transaction", item.TargetId, item.Reason, item.EffectiveAt, recordedAt, source, externalId, note);
        repository.MarkReversed(userId, "transaction", item.TargetId, correctionId);
        return correctionId;
    }

    private long CorrectDividend(string userId, LedgerCorrectionInput item, DateTime recordedAt, string source, string externalId, string? note)
    {
        var row = repository.GetDividendForCorrection(userId, item.TargetId)
            ?? throw new DividendNotFoundError();
        if (row.AlreadyReversed)
        {
            throw new ArgumentException("该分红已经冲正");
        }

        var cash = repository.GetCashState(userId, row.CashSymbol);
        var effectiveDate = DateOnly.FromDateTime(item.EffectiveAt.UtcDateTime.AddHours(8));
        if (item.EffectiveAt < row.PaidAt || cash.AsOfDate > effectiveDate)
        {
            throw new LedgerStateError(ErrorCodes.LedgerCorrectionDateInvalid);
        }

        if (cash.Balance < row.NetAmount)
        {
            throw new ArgumentException("冲正将导致现金为负");
        }

        repository.WriteCashPosition(userId, cash.Symbol, cash.Balance - row.NetAmount, effectiveDate, recordedAt);
        var correctionId = repository.InsertCorrection(userId, "dividend", item.TargetId, item.Reason, item.EffectiveAt, recordedAt, source, externalId, note);
        repository.MarkReversed(userId, "dividend", item.TargetId, correctionId);
        return correctionId;
    }

    private static decimal? ExtractOriginalAverage(string? beforeJson)
    {
        if (beforeJson is null)
        {
            return null;
        }

        try
        {
            using var doc = JsonDocument.Parse(beforeJson);
            return doc.RootElement.TryGetProperty("average_cost", out var el) && el.ValueKind == JsonValueKind.Number
                ? el.GetDecimal()
                : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    /// <summary>记录一笔公司行动（红利再投资/基金转换/拆分/转托管），返回事件 ID；幂等。</summary>
    public long RecordCorporateAction(string userId, CorporateActionInput item) =>
        repository.ExecuteAtomically(userId, scoped => new TransactionService(scoped, timeProvider).RecordCorporateActionCore(userId, item));

    private long RecordCorporateActionCore(string userId, CorporateActionInput item)
    {
        var actionType = item.ActionType.Trim().ToLowerInvariant();
        if (!CorporateActionRules.IsValidActionType(actionType))
        {
            throw new ArgumentException("不支持的公司行动类型");
        }

        if (item.AssetType is not (AssetType.Stock or AssetType.Fund))
        {
            throw new ArgumentException("公司行动只接受股票或基金");
        }

        var symbol = item.Symbol.Trim().ToUpperInvariant();
        var targetSymbol = string.IsNullOrWhiteSpace(item.TargetSymbol) ? null : item.TargetSymbol.Trim().ToUpperInvariant();
        if (actionType == "fund_conversion" && targetSymbol == symbol)
        {
            throw new ArgumentException("基金转换的目标不能与来源相同");
        }

        if (item.EffectiveAt.Offset != TimeSpan.FromHours(8))
        {
            throw new ArgumentException("公司行动时间必须为上海时区时间");
        }

        var recordedAt = NowUtc();
        if (item.EffectiveAt > recordedAt)
        {
            throw new ArgumentException("公司行动时间不能晚于记录时间");
        }

        var source = item.Source.Trim().ToLowerInvariant();
        var externalId = item.ExternalId.Trim();
        if (source.Length == 0 || externalId.Length == 0)
        {
            throw new ArgumentException("公司行动来源和编号不能为空");
        }

        if (item.GrossAmount < 0 || item.Fees < 0)
        {
            throw new ArgumentException("公司行动金额和费用不能为负");
        }

        if (item.Quantity is <= 0 || item.TargetQuantity is <= 0 || item.Price is <= 0
            || item.TargetPrice is <= 0 || item.Ratio is <= 0)
            throw new ArgumentException("公司行动数量、价格和折算比例必须大于0");

        var note = string.IsNullOrWhiteSpace(item.Note) ? null : item.Note.Trim();
        var effectiveDate = DateOnly.FromDateTime(item.EffectiveAt.UtcDateTime.AddHours(8));

        // 幂等
        var duplicate = repository.FindCorporateAction(userId, source, externalId);
        if (duplicate is not null)
        {
            var request = repository.ReadRecordedRequest(userId, "corporate_action", duplicate.Value.ToString());
            LedgerIdempotency.RequireSame(request,
                LedgerRequests.Corporate(item, item.CashSymbol?.Trim().ToUpperInvariant() ?? LedgerIdempotency.RecordedCash(request)),
                null, "quantity", "target_quantity");
            return duplicate.Value;
        }

        var latest = repository.LatestValuationDate(userId);
        if (latest is not null && effectiveDate <= latest)
        {
            throw new ArgumentException("公司行动日期不得早于或等于最近已确认估值日");
        }

        repository.RequireKnownInstrument(userId, symbol, item.AssetType);
        var sourcePosition = repository.GetPositionState(userId, symbol);
        if (sourcePosition is null && actionType != "custody_transfer")
        {
            throw new ArgumentException($"公司行动标的没有当前持仓：{symbol}");
        }

        decimal cashDelta = 0m;
        var storedQuantity = item.Quantity;
        var storedTargetQuantity = item.TargetQuantity;
        var storedRatio = item.Ratio;
        var storedCashSymbol = item.CashSymbol is null ? null : item.CashSymbol.Trim().ToUpperInvariant();
        var price = item.Price;
        var targetPrice = item.TargetPrice ?? (actionType == "fund_conversion" ? item.Price : null);

        switch (actionType)
        {
            case "dividend_reinvestment":
            {
                if (price is null)
                {
                    throw new ArgumentException("红利再投资必须提供再投资价格");
                }

                if (item.GrossAmount <= 0)
                {
                    throw new ArgumentException("红利再投资金额必须大于0");
                }

                storedQuantity ??= CorporateActionRules.ReinvestmentQuantity(item.GrossAmount, price.Value);
                if (storedQuantity <= 0)
                {
                    throw new ArgumentException("红利再投资数量必须大于0");
                }

                var cash = repository.GetCashState(userId, item.CashSymbol);
                storedCashSymbol = cash.Symbol;
                cashDelta = -(item.GrossAmount + item.Fees);
                if (cash.Balance + cashDelta < 0)
                {
                    throw new ArgumentException("可用现金不足以完成红利再投资");
                }

                var oldQuantity = sourcePosition!.Quantity;
                var oldCost = sourcePosition.AverageCost;
                var newQuantity = oldQuantity + storedQuantity.Value;
                var newCost = CorporateActionRules.ReinvestmentCost(oldQuantity, oldCost, item.GrossAmount, item.Fees, newQuantity);
                repository.UpsertPosition(userId, symbol, item.AssetType, newQuantity, newCost, newQuantity * price.Value, effectiveDate, recordedAt);
                repository.WriteCashPosition(userId, cash.Symbol, cash.Balance + cashDelta, effectiveDate, recordedAt);
                break;
            }
            case "fund_conversion":
            {
                if (item.AssetType != AssetType.Fund)
                {
                    throw new ArgumentException("基金转换的来源必须是基金");
                }

                if (targetSymbol is null || storedTargetQuantity is null)
                {
                    throw new ArgumentException("基金转换必须提供目标代码和目标数量");
                }

                repository.RequireKnownInstrument(userId, targetSymbol, AssetType.Fund);
                var oldQuantity = sourcePosition!.Quantity;
                storedQuantity ??= oldQuantity;
                if (storedQuantity > oldQuantity)
                {
                    throw new ArgumentException("基金转换数量超过当前持仓");
                }

                var oldCost = sourcePosition.AverageCost;
                var transferredCost = oldCost * storedQuantity.Value;
                var remaining = oldQuantity - storedQuantity.Value;
                if (remaining == 0)
                {
                    repository.DeletePosition(userId, symbol);
                }
                else
                {
                    repository.UpsertPosition(userId, symbol, item.AssetType, remaining, oldCost, sourcePosition.MarketValue * remaining / oldQuantity, effectiveDate, recordedAt);
                }

                var targetPosition = repository.GetPositionState(userId, targetSymbol);
                var targetOldQuantity = targetPosition?.Quantity ?? 0m;
                var targetOldCost = targetPosition?.AverageCost ?? 0m;
                var newTargetQuantity = targetOldQuantity + storedTargetQuantity.Value;
                var newTargetCost = CorporateActionRules.ConversionTargetCost(targetOldQuantity, targetOldCost, transferredCost, newTargetQuantity);
                var targetMark = targetPrice is not null ? newTargetQuantity * targetPrice.Value : newTargetQuantity * newTargetCost;
                repository.UpsertPosition(userId, targetSymbol, AssetType.Fund, newTargetQuantity, newTargetCost, targetMark, effectiveDate, recordedAt);
                break;
            }
            case "split":
            {
                if (storedRatio is null)
                {
                    throw new ArgumentException("拆分折算必须提供正数比例");
                }

                var oldQuantity = sourcePosition!.Quantity;
                storedQuantity ??= oldQuantity;
                if (storedQuantity > oldQuantity)
                {
                    throw new ArgumentException("拆分数量超过当前持仓");
                }

                storedTargetQuantity = storedQuantity * storedRatio;
                var newTotal = oldQuantity - storedQuantity.Value + storedTargetQuantity.Value;
                var oldCostBasis = oldQuantity * sourcePosition.AverageCost;
                var newCost = CorporateActionRules.SplitCost(oldQuantity, sourcePosition.AverageCost, newTotal);
                var mark = price is null ? sourcePosition.MarketValue : newTotal * price.Value;
                repository.UpsertPosition(userId, symbol, item.AssetType, newTotal, newCost, mark, effectiveDate, recordedAt);
                break;
            }
            case "custody_transfer":
            {
                if (storedQuantity is null || storedQuantity <= 0)
                {
                    throw new ArgumentException("转托管必须提供正数数量");
                }

                if (sourcePosition is not null && storedQuantity > sourcePosition.Quantity)
                {
                    throw new ArgumentException("转托管数量超过当前持仓");
                }

                if (string.IsNullOrWhiteSpace(item.FromAccount) || string.IsNullOrWhiteSpace(item.ToAccount))
                {
                    throw new ArgumentException("转托管必须填写转出和转入账户");
                }

                break;
            }
        }

        var actionId = repository.InsertCorporateAction(userId, new CorporateActionRecord(
            actionType, symbol, targetSymbol, item.AssetType, storedQuantity, storedTargetQuantity, price, targetPrice,
            item.GrossAmount, item.Fees, cashDelta, storedRatio, item.EffectiveAt, source, externalId, storedCashSymbol,
            item.FromAccount, item.ToAccount, note));
        repository.InsertAudit(userId, "corporate_action", actionId.ToString(), recordedAt, null, LedgerIdempotency.WithRequest(JsonSerializer.Serialize(new
        {
            action_type = actionType,
            symbol,
            target_symbol = targetSymbol,
            quantity = storedQuantity,
            target_quantity = storedTargetQuantity,
            cash_delta = cashDelta,
            external_id = externalId,
        }), LedgerRequests.Corporate(item, storedCashSymbol)));
        return actionId;
    }

    /// <summary>记录一笔外部资金流（入金/出金），返回 event_id；幂等（同 event_id）。</summary>
    public string RecordExternalCashFlow(string userId, ExternalCashFlowInput item) =>
        repository.ExecuteAtomically(userId, scoped => new TransactionService(scoped, timeProvider).RecordExternalCashFlowCore(userId, item));

    private string RecordExternalCashFlowCore(string userId, ExternalCashFlowInput item)
    {
        var eventId = item.EventId.Trim();
        if (eventId.Length == 0)
        {
            throw new ArgumentException("资金流确认键不能为空");
        }

        if (item.Amount <= 0)
        {
            throw new ArgumentException("资金流金额必须大于0");
        }

        if (string.IsNullOrWhiteSpace(item.Reason))
        {
            throw new ArgumentException("资金流原因不能为空");
        }

        var signedAmount = item.FlowType == CashFlowType.Deposit ? item.Amount : -item.Amount;
        var recordedAt = NowUtc();

        var existing = repository.FindPortfolioEvent(userId, eventId);
        if (existing is not null)
        {
            var request = repository.ReadRecordedRequest(userId, "external_cash_flow", eventId);
            LedgerIdempotency.RequireSame(request,
                LedgerRequests.Flow(item, item.CashSymbol?.Trim().ToUpperInvariant() ?? LedgerIdempotency.RecordedCash(request)));
            return eventId;
        }

        var latest = repository.LatestValuationDate(userId);
        if (latest is null)
        {
            throw new ArgumentException("请先完成净值基线对应的首个有效日决策");
        }

        if (item.EffectiveDate <= latest)
        {
            throw new ArgumentException("资金流日期不得早于或等于最近已确认估值日");
        }

        var cash = repository.GetCashState(userId, item.CashSymbol);
        var newCash = cash.Balance + signedAmount;
        if (newCash < 0)
        {
            throw new ArgumentException("出金金额超过当前现金余额");
        }

        repository.WriteCashPosition(userId, cash.Symbol, newCash, item.EffectiveDate, recordedAt);
        var detailsJson = JsonSerializer.Serialize(new { cash_symbol = cash.Symbol });
        repository.InsertCashFlowEvent(userId, eventId, item.FlowType == CashFlowType.Deposit ? "deposit" : "withdrawal", item.EffectiveDate, signedAmount, item.Reason.Trim(), detailsJson, recordedAt);
        repository.InsertAudit(userId, "external_cash_flow", eventId, recordedAt, null, LedgerIdempotency.WithRequest(JsonSerializer.Serialize(new
        {
            event_id = eventId,
            flow_type = item.FlowType == CashFlowType.Deposit ? "deposit" : "withdrawal",
            amount = signedAmount,
            effective_date = item.EffectiveDate,
            cash_symbol = cash.Symbol,
            cash_before = cash.Balance,
            cash_after = newCash,
            reason = item.Reason.Trim(),
        }), LedgerRequests.Flow(item, cash.Symbol)));
        return eventId;
    }

    /// <summary>组合校准：用完整对账快照整体替换持仓，返回 event_id；幂等。</summary>
    public string RecordPortfolioCalibration(string userId, PortfolioCalibrationInput item) =>
        repository.ExecuteAtomically(userId, scoped => new TransactionService(scoped, timeProvider).RecordPortfolioCalibrationCore(userId, item));

    private string RecordPortfolioCalibrationCore(string userId, PortfolioCalibrationInput item)
    {
        var eventId = item.EventId.Trim();
        if (eventId.Length == 0)
        {
            throw new ArgumentException("校准确认键不能为空");
        }

        if (string.IsNullOrWhiteSpace(item.Reason))
        {
            throw new ArgumentException("校准原因不能为空");
        }

        var totalValue = item.Positions.Sum(p => p.MarketValue);
        if (totalValue - item.NetExternalFlow <= 0)
        {
            throw new ArgumentException("扣除校准净外部资金流后的组合价值必须为正");
        }

        // Retain the old digest only to verify untouched historical records.
        var legacySnapshotJson = JsonSerializer.Serialize(item.Positions.OrderBy(p => p.Symbol).Select(p => new
        {
            symbol = p.Symbol,
            asset_type = p.AssetType.ToString().ToLowerInvariant(),
            quantity = p.Quantity,
            average_cost = p.AverageCost,
            market_value = p.MarketValue,
        }));
        var legacyFingerprint = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(legacySnapshotJson))).ToLowerInvariant();
        var ordered = item.Positions.Select(p => p with { Symbol = p.Symbol.Trim().ToUpperInvariant() })
            .OrderBy(p => p.Symbol, StringComparer.Ordinal).ToList();
        if (ordered.Select(p => p.Symbol).Distinct(StringComparer.Ordinal).Count() != ordered.Count)
            throw new ArgumentException("校准快照不得包含重复标的");
        var fingerprint = LedgerIdempotency.Hash(JsonSerializer.SerializeToElement(ordered.Select(p => new
        {
            symbol = p.Symbol, asset_type = p.AssetType.ToString().ToLowerInvariant(), quantity = p.Quantity,
            average_cost = p.AverageCost, market_value = p.MarketValue, as_of_date = p.AsOfDate,
        })));
        var detailsJson = JsonSerializer.Serialize(new { positions_fingerprint = fingerprint, symbols = ordered.Select(p => p.Symbol) });
        var recordedAt = NowUtc();

        var existing = repository.FindPortfolioEvent(userId, eventId);
        if (existing is not null)
        {
            var request = repository.ReadRecordedRequest(userId, "portfolio_calibration", eventId);
            try { LedgerIdempotency.RequireSame(request, LedgerRequests.Calibration(item, fingerprint)); }
            catch (ApplicationFailure) when (request?.Fingerprint is null)
            {
                LedgerIdempotency.RequireSame(request, LedgerRequests.Calibration(item, fingerprint), LedgerRequests.Calibration(item, legacyFingerprint));
            }
            return eventId;
        }

        var latest = repository.LatestValuationDate(userId);
        if (latest is null)
        {
            throw new ArgumentException("组合校准前必须先建立首个有效净值");
        }

        if (item.AsOfDate <= latest)
        {
            throw new ArgumentException("校准日期不得早于或等于最近已确认估值日");
        }

        var before = repository.ListPositions(userId);
        repository.ReplaceAllPositions(userId, ordered, item.AsOfDate, recordedAt);
        repository.InsertCalibrationEvent(userId, eventId, item.AsOfDate, item.NetExternalFlow, item.Reason.Trim(), detailsJson, recordedAt);
        repository.InsertAudit(userId, "portfolio_calibration", eventId, recordedAt, JsonSerializer.Serialize(before), LedgerIdempotency.WithRequest(JsonSerializer.Serialize(new
        {
            event_id = eventId,
            as_of_date = item.AsOfDate,
            net_external_flow = item.NetExternalFlow,
            positions_fingerprint = fingerprint,
            reason = item.Reason.Trim(),
        }), LedgerRequests.Calibration(item, fingerprint)));
        return eventId;
    }

    private DateTime NowUtc() =>
        timeProvider.GetUtcNow().UtcDateTime;

    private static string? BeforeJson(PositionState? current) =>
        current is null ? null : JsonSerializer.Serialize(new
        {
            symbol = current.Symbol,
            asset_type = current.AssetType.ToString().ToLowerInvariant(),
            quantity = current.Quantity,
            average_cost = current.AverageCost,
            market_value = current.MarketValue,
            as_of_date = current.AsOfDate,
        });

    private static string AfterJson(ValidatedTransaction v, CashState cash, decimal newCash, DateTime recordedAt) =>
        JsonSerializer.Serialize(new
        {
            source = v.Source,
            external_id = v.ExternalId,
            symbol = v.Symbol,
            asset_type = v.AssetType.ToString().ToLowerInvariant(),
            action = v.Action.ToString().ToLowerInvariant(),
            quantity = v.Quantity,
            price = v.Price,
            fees = v.Fees,
            gross_amount = v.GrossAmount,
            cash_delta = v.CashDelta,
            cash_symbol = cash.Symbol,
            cash_before = cash.Balance,
            cash_after = newCash,
            traded_at = v.TradedAt,
            note = v.Note,
        });
}
