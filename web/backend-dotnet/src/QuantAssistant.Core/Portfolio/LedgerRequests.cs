using System.Globalization;
using System.Text.Json;

namespace QuantAssistant.Core.Portfolio;

internal static class LedgerRequests
{
    private static string? Text(string? value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    private static string At(DateTimeOffset value) => value.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture);
    private static string Day(DateOnly value) => value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    internal static JsonElement Transaction(ValidatedTransaction v, string? cash) => JsonSerializer.SerializeToElement(new
    {
        symbol = v.Symbol, asset_type = v.AssetType.ToString().ToLowerInvariant(), action = v.Action.ToString().ToLowerInvariant(),
        quantity = v.Quantity, price = v.Price, fees = v.Fees, traded_at = At(v.TradedAt), cash_symbol = cash,
        source = v.Source, external_id = v.ExternalId, note = v.Note
    });

    internal static JsonElement Dividend(ValidatedDividend v, string? cash) => JsonSerializer.SerializeToElement(new
    {
        symbol = v.Symbol, asset_type = v.AssetType.ToString().ToLowerInvariant(), gross_amount = v.GrossAmount,
        fees = v.Fees, paid_at = At(v.PaidAt), cash_symbol = cash, source = v.Source, external_id = v.ExternalId, note = v.Note
    });

    internal static JsonElement Correction(LedgerCorrectionInput v) => JsonSerializer.SerializeToElement(new
    {
        target_type = v.TargetType.Trim().ToLowerInvariant(), target_id = v.TargetId, reason = v.Reason.Trim(),
        effective_at = At(v.EffectiveAt), source = v.Source.Trim().ToLowerInvariant(), external_id = v.ExternalId.Trim(), note = Text(v.Note)
    });

    internal static JsonElement Corporate(CorporateActionInput v, string? cash) => JsonSerializer.SerializeToElement(new
    {
        action_type = v.ActionType.Trim().ToLowerInvariant(), symbol = v.Symbol.Trim().ToUpperInvariant(),
        target_symbol = Text(v.TargetSymbol)?.ToUpperInvariant(), asset_type = v.AssetType.ToString().ToLowerInvariant(),
        quantity = v.Quantity, target_quantity = v.TargetQuantity, price = v.Price,
        target_price = v.TargetPrice ?? (v.ActionType.Trim().Equals("fund_conversion", StringComparison.OrdinalIgnoreCase) ? v.Price : null),
        gross_amount = v.GrossAmount, fees = v.Fees, ratio = v.Ratio, effective_at = At(v.EffectiveAt), cash_symbol = cash,
        from_account = Text(v.FromAccount), to_account = Text(v.ToAccount), source = v.Source.Trim().ToLowerInvariant(), external_id = v.ExternalId.Trim(), note = Text(v.Note)
    });

    internal static JsonElement Flow(ExternalCashFlowInput v, string? cash) => JsonSerializer.SerializeToElement(new
    {
        event_id = v.EventId.Trim(), event_type = v.FlowType == CashFlowType.Deposit ? "deposit" : "withdrawal",
        amount = v.FlowType == CashFlowType.Deposit ? v.Amount : -v.Amount, effective_date = Day(v.EffectiveDate),
        reason = v.Reason.Trim(), cash_symbol = cash, status = "confirmed"
    });

    internal static JsonElement Calibration(PortfolioCalibrationInput v, string fingerprint) => JsonSerializer.SerializeToElement(new
    {
        event_id = v.EventId.Trim(), event_type = "calibration", effective_date = Day(v.AsOfDate),
        amount = v.NetExternalFlow, reason = v.Reason.Trim(), positions_fingerprint = fingerprint, status = "confirmed"
    });
}
