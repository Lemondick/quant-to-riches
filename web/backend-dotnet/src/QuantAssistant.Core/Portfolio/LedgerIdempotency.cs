using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Core.Portfolio;

/// <summary>Canonical requests stored only on new audit events; historic rows remain unchanged.</summary>
public static class LedgerIdempotency
{
    public static ApplicationFailure Conflict() => new("idempotency_conflict", 409, "相同事件编号对应不同内容，请核对原始记录或使用新的编号。");

    public static string Hash(JsonElement value)
    {
        using var bytes = new MemoryStream();
        using (var writer = new Utf8JsonWriter(bytes)) Write(writer, value);
        return Convert.ToHexString(SHA256.HashData(bytes.ToArray())).ToLowerInvariant();
    }

    private static void Write(Utf8JsonWriter writer, JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (var property in value.EnumerateObject().OrderBy(p => p.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    Write(writer, property.Value);
                }
                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (var item in value.EnumerateArray()) Write(writer, item);
                writer.WriteEndArray();
                break;
            case JsonValueKind.Number:
                writer.WriteRawValue(value.GetDecimal().ToString("G29", CultureInfo.InvariantCulture));
                break;
            default: value.WriteTo(writer); break;
        }
    }

    public static string WithRequest(string auditJson, JsonElement request)
    {
        var audit = JsonNode.Parse(auditJson)!.AsObject();
        audit["request_fingerprint"] = "v1:" + Hash(request);
        return audit.ToJsonString();
    }

    public static string? RecordedCash(RecordedLedgerRequest? value)
    {
        if (value is null) return null;
        using var document = JsonDocument.Parse(value.RecordJson);
        var cash = Field(document.RootElement, "cash_symbol");
        return cash.ValueKind == JsonValueKind.String ? cash.GetString()?.Trim().ToUpperInvariant() : null;
    }

    public static void RequireSame(RecordedLedgerRequest? value, JsonElement request,
        JsonElement? legacyRequest = null, params string[] derivedNullable)
    {
        if (value is null) throw Conflict();
        if (value.Fingerprint is not null)
        {
            if (value.Fingerprint != "v1:" + Hash(request)) throw Conflict();
            return;
        }
        using var document = JsonDocument.Parse(value.RecordJson);
        foreach (var property in (legacyRequest ?? request).EnumerateObject())
        {
            if (property.Value.ValueKind == JsonValueKind.Null && derivedNullable.Contains(property.Name)) continue;
            if (!Equivalent(property.Name, property.Value, Field(document.RootElement, property.Name))) throw Conflict();
        }
    }

    private static JsonElement Field(JsonElement row, string name)
    {
        if (row.TryGetProperty(name, out var value)) return value;
        return row.TryGetProperty("details_json", out var details) && details.ValueKind == JsonValueKind.Object
            && details.TryGetProperty(name, out value) ? value : default;
    }

    private static bool Equivalent(string name, JsonElement expected, JsonElement actual)
    {
        if (actual.ValueKind == JsonValueKind.Undefined) return false;
        if (expected.ValueKind == JsonValueKind.Number)
            return actual.ValueKind == JsonValueKind.Number && expected.GetDecimal() == actual.GetDecimal();
        if ((expected.ValueKind is JsonValueKind.String or JsonValueKind.Null) && (actual.ValueKind is JsonValueKind.String or JsonValueKind.Null))
        {
            static string? Text(JsonElement item) => item.ValueKind == JsonValueKind.Null || string.IsNullOrWhiteSpace(item.GetString()) ? null : item.GetString()!.Trim();
            var left = Text(expected); var right = Text(actual);
            if (name.EndsWith("_at", StringComparison.Ordinal))
                return DateTimeOffset.TryParse(left, CultureInfo.InvariantCulture, DateTimeStyles.None, out var first)
                    && DateTimeOffset.TryParse(right, CultureInfo.InvariantCulture, DateTimeStyles.None, out var second) && first == second;
            return name is "symbol" or "target_symbol" or "cash_symbol" or "source" or "asset_type" or "action" or "action_type" or "target_type" or "event_type"
                ? string.Equals(left, right, StringComparison.OrdinalIgnoreCase) : left == right;
        }
        return expected.ValueKind == actual.ValueKind && expected.GetRawText() == actual.GetRawText();
    }
}
