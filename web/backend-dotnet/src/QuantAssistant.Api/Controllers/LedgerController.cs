using Microsoft.AspNetCore.Mvc;
using System.Globalization;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using Action = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Api.Controllers;

/// <summary>账本写接口（对应旧站点 routes.py 的 ledger 域），复用 P3 TransactionService。</summary>
[ApiController]
[Route("api/v1/ledger")]
public sealed class LedgerController(TransactionService transactions, ILedgerRepository ledger) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> GetLedger([FromQuery] int limit = 200, CancellationToken cancellation = default)
    {
        var snapshot = await ledger.ReadLedgerAsync(CurrentUserId(), limit, cancellation);
        return Ok(new { entries = snapshot.Entries.Select(EntryResponse), total_fees = DecimalText(snapshot.TotalFees), total_dividends = DecimalText(snapshot.TotalDividends) });
    }

    [HttpGet("transactions/{id:long}")]
    public IActionResult GetTransaction(long id) => Ok(EntryResponse(ledger.FindLedgerEntry(CurrentUserId(), id, false) ?? throw new TransactionNotFoundError()));

    [HttpGet("dividends/{id:long}")]
    public IActionResult GetDividend(long id) => Ok(EntryResponse(ledger.FindLedgerEntry(CurrentUserId(), id, true) ?? throw new DividendNotFoundError()));

    [HttpPost("transactions")]
    public IActionResult RecordTransaction([FromBody] LedgerTradeBody body)
    {
        var assetType = ParseAssetType(body.AssetType);
        var input = new TransactionInput(
            Symbols.Canonical(body.Symbol, assetType),
            assetType,
            ParseAction(body.EntryType),
            body.Quantity,
            body.Price,
            body.Fees,
            body.OccurredAt,
            body.ExternalId,
            body.EntryType == "dca" ? "dca" : "manual-api",
            null,
            body.Note);

        var userId = CurrentUserId();
        var id = transactions.RecordTransaction(userId, input, body.Name);
        return Created($"/api/v1/ledger/transactions/{id}", EntryResponse(ledger.FindLedgerEntry(userId, id, false)!));
    }

    [HttpPost("dividends")]
    public IActionResult RecordDividend([FromBody] LedgerDividendBody body)
    {
        var assetType = ParseAssetType(body.AssetType);
        var input = new DividendInput(
            Symbols.Canonical(body.Symbol, assetType),
            assetType,
            body.GrossAmount,
            body.Fees,
            body.OccurredAt,
            body.ExternalId,
            "manual-api",
            null,
            body.Note);

        var userId = CurrentUserId();
        var id = transactions.RecordDividend(userId, input);
        return Created($"/api/v1/ledger/dividends/{id}", EntryResponse(ledger.FindLedgerEntry(userId, id, true)!));
    }

    [HttpPost("actions")]
    public IActionResult RecordCorporateAction([FromBody] CorporateActionBody body)
    {
        var assetType = ParseAssetType(body.AssetType);
        var input = new CorporateActionInput(
            body.ActionType,
            Symbols.Canonical(body.Symbol, assetType),
            assetType,
            body.EffectiveAt,
            body.ExternalId,
            "manual-api",
            string.IsNullOrWhiteSpace(body.TargetSymbol) ? null : Symbols.Canonical(body.TargetSymbol, assetType),
            body.Quantity,
            body.TargetQuantity,
            body.Price,
            body.TargetPrice,
            body.GrossAmount ?? 0m,
            body.Fees ?? 0m,
            body.Ratio,
            body.CashSymbol,
            body.FromAccount,
            body.ToAccount,
            body.Note);

        var id = transactions.RecordCorporateAction(CurrentUserId(), input);
        return Created($"/api/v1/ledger/actions/{id}", new { id });
    }

    [HttpPost("corrections")]
    public IActionResult CorrectLedgerEntry([FromBody] LedgerCorrectionBody body)
    {
        var input = new LedgerCorrectionInput(
            body.TargetType,
            body.TargetId,
            body.Reason,
            body.EffectiveAt,
            body.ExternalId,
            "manual-api",
            body.Note);

        var id = transactions.CorrectLedgerEntry(CurrentUserId(), input);
        return Created($"/api/v1/ledger/corrections/{id}", new { id });
    }

    private static AssetType ParseAssetType(string value) => value switch
    {
        "stock" => AssetType.Stock,
        "fund" => AssetType.Fund,
        "index" => AssetType.Index,
        "cash" => AssetType.Cash,
        _ => throw new ArgumentException($"unknown asset_type: {value}"),
    };

    private static string? DecimalText(decimal? value) => value?.ToString(CultureInfo.InvariantCulture);

    private static object EntryResponse(LedgerEntry entry) => new
    {
        entry.Id, entry.EntryType, entry.Symbol, entry.Name,
        asset_type = entry.AssetType.ToString().ToLowerInvariant(),
        quantity = DecimalText(entry.Quantity), price = DecimalText(entry.Price),
        gross_amount = DecimalText(entry.GrossAmount), fees = DecimalText(entry.Fees), net_amount = DecimalText(entry.NetAmount),
        entry.OccurredAt, entry.ExternalId, entry.Note, entry.Reversed,
    };

    private static Action ParseAction(string entryType) => entryType switch
    {
        "buy" => Action.Buy,
        "sell" => Action.Sell,
        "dca" => Action.Add,
        _ => throw new ArgumentException($"unknown entry_type: {entryType}"),
    };

    private string CurrentUserId() => User.FindFirst("sub")?.Value ?? User.FindFirst("user_id")?.Value ?? throw new QuantAssistant.Core.Auth.AuthFailure("authentication_required");
}

/// <summary>成交录入请求（对应 LedgerTradeBody）。</summary>
public sealed class LedgerTradeBody
{
    public string EntryType { get; set; } = "";
    public string Symbol { get; set; } = "";
    public string Name { get; set; } = "";
    public string AssetType { get; set; } = "";
    public decimal Quantity { get; set; }
    public decimal Price { get; set; }
    public decimal Fees { get; set; }
    public DateTimeOffset OccurredAt { get; set; }
    public string ExternalId { get; set; } = "";
    public string? Note { get; set; }
}

/// <summary>分红录入请求（对应 LedgerDividendBody）。</summary>
public sealed class LedgerDividendBody
{
    public string Symbol { get; set; } = "";
    public string AssetType { get; set; } = "";
    public decimal GrossAmount { get; set; }
    public decimal Fees { get; set; }
    public DateTimeOffset OccurredAt { get; set; }
    public string ExternalId { get; set; } = "";
    public string? Note { get; set; }
}

/// <summary>公司行动录入请求（对应 CorporateActionBody）。</summary>
public sealed class CorporateActionBody
{
    public string ActionType { get; set; } = "";
    public string Symbol { get; set; } = "";
    public string AssetType { get; set; } = "";
    public DateTimeOffset EffectiveAt { get; set; }
    public string ExternalId { get; set; } = "";
    public string? TargetSymbol { get; set; }
    public decimal? Quantity { get; set; }
    public decimal? TargetQuantity { get; set; }
    public decimal? Price { get; set; }
    public decimal? TargetPrice { get; set; }
    public decimal? GrossAmount { get; set; }
    public decimal? Fees { get; set; }
    public decimal? Ratio { get; set; }
    public string? CashSymbol { get; set; }
    public string? FromAccount { get; set; }
    public string? ToAccount { get; set; }
    public string? Note { get; set; }
}

/// <summary>账本冲正请求（对应 LedgerCorrectionBody）。</summary>
public sealed class LedgerCorrectionBody
{
    public string TargetType { get; set; } = "";
    public long TargetId { get; set; }
    public string Reason { get; set; } = "";
    public DateTimeOffset EffectiveAt { get; set; }
    public string ExternalId { get; set; } = "";
    public string? Note { get; set; }
}
