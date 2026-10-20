using System.Collections.Frozen;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace QuantAssistant.Core.Market;

public sealed record IndexProviderMapping(string Provider, string Symbol, string ResponseSymbol,
    IReadOnlyList<string> ResponseNames, string ResponseType, string ResponseMarket, string? ResponseCurrency,
    string CurrencyBasis, string SourceTimezone, string VerificationStatus, string VerifiedAt, IReadOnlyList<string> EvidenceUrls);

public sealed record IndexDefinition(string Id, string Name, string OfficialName, string Market, string Currency,
    string Timezone, string CalendarId, string ExchangeCode, int DisplayOrder, bool Enabled, string ProductStatus,
    string ProductEvidence, IndexProviderMapping Quote, IndexProviderMapping? History, string? LegacyHistorySymbol);

/// <summary>Loads the same packaged JSON authority used by Python; callers cannot modify stored definitions.</summary>
public static class IndexRegistry
{
    private sealed record Root(int FormatVersion, string RegistryVersion, IReadOnlyList<IndexDefinition> Definitions);
    private static readonly JsonSerializerOptions Options = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private static readonly Lazy<Root> Registry = new(Load);
    private static readonly Lazy<FrozenDictionary<string, IndexDefinition>> ById = new(() => All.ToFrozenDictionary(row => row.Id));
    public static string Version => Registry.Value.RegistryVersion;
    public static IReadOnlyList<IndexDefinition> All => Registry.Value.Definitions;
    public static IndexDefinition Get(string id) => ById.Value.GetValueOrDefault(id)
        ?? throw new ArgumentException("指数标识无效");
    public static IndexDefinition ResolveLegacy(string symbol) => All.SingleOrDefault(row => row.Market == "CN" && row.ExchangeCode == symbol)
        ?? throw new ArgumentException("指数代码无效");
    public static JsonArray JsonDefinitions() => JsonSerializer.SerializeToNode(All, Options)!.AsArray();

    private static IndexProviderMapping Freeze(IndexProviderMapping value)
    {
        if (value.Provider is not ("tencent" or "yahoo") || value.VerificationStatus is not ("verified" or "pending" or "unavailable")
            || string.IsNullOrEmpty(value.Symbol) || string.IsNullOrEmpty(value.ResponseSymbol)
            || value.ResponseNames is not { Count: > 0 } || value.ResponseNames.Any(string.IsNullOrEmpty)
            || value.EvidenceUrls is not { Count: > 0 } || value.EvidenceUrls.Any(url => !url.StartsWith("https://", StringComparison.Ordinal))
            || string.IsNullOrEmpty(value.CurrencyBasis) || string.IsNullOrEmpty(value.VerifiedAt))
            throw new InvalidOperationException("Invalid index provider mapping");
        return value with { ResponseNames = Array.AsReadOnly(value.ResponseNames.ToArray()), EvidenceUrls = Array.AsReadOnly(value.EvidenceUrls.ToArray()) };
    }
    private static Root Load()
    {
        using var resource = typeof(IndexRegistry).Assembly.GetManifestResourceStream("QuantAssistant.IndexRegistry")
            ?? throw new InvalidOperationException("Index registry resource missing");
        var root = JsonSerializer.Deserialize<Root>(resource, Options) ?? throw new InvalidOperationException("Index registry missing");
        if (root.FormatVersion != 1 || string.IsNullOrEmpty(root.RegistryVersion) || root.Definitions is not { Count: 16 })
            throw new InvalidOperationException("Invalid index registry format");
        var rows = root.Definitions.Select(row => row with { Quote = Freeze(row.Quote), History = row.History is null ? null : Freeze(row.History) }).OrderBy(row => row.DisplayOrder).ToArray();
        if (rows.Select(row => row.Id).Distinct().Count() != rows.Length || rows.Select(row => row.DisplayOrder).Distinct().Count() != rows.Length
            || rows.Where(row => row.Market == "CN").Select(row => row.ExchangeCode).Distinct().Count() != rows.Count(row => row.Market == "CN"))
            throw new InvalidOperationException("Index registry identities or order are not unique");
        foreach (var row in rows)
        {
            var currency = row.Market switch { "CN" => "CNY", "HK" => "HKD", "JP" => "JPY", "US" => "USD", _ => null };
            if (currency is null || !row.Id.StartsWith(row.Market.ToLowerInvariant() + ".", StringComparison.Ordinal)
                || row.Currency != currency || row.Timezone != MarketSessions.ZoneId(row.Market) || row.CalendarId != row.Market
                || row.Quote.SourceTimezone != row.Timezone || row.ProductStatus is not ("confirmed" or "provisional")
                || string.IsNullOrEmpty(row.Name) || string.IsNullOrEmpty(row.OfficialName) || string.IsNullOrEmpty(row.ExchangeCode)
                || row.Enabled && row.Quote.VerificationStatus != "verified"
                || row.History is not null && row.History.SourceTimezone != row.Timezone
                || row.LegacyHistorySymbol is not null && (row.Market != "CN" || row.LegacyHistorySymbol != row.ExchangeCode || row.History?.VerificationStatus != "verified"))
                throw new InvalidOperationException("Invalid index registry definition");
        }
        return root with { Definitions = Array.AsReadOnly(rows) };
    }
}
