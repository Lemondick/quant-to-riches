using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Market;

namespace QuantAssistant.Api.Services;

/// <summary>Projects historical public snapshots through the packaged index authority.</summary>
public static class PublicIndexProjection
{
    private static string? Text(JsonObject row, string key) => row[key] is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;
    private static bool Number(JsonObject row, string key, out decimal number)
        => decimal.TryParse(Text(row, key), NumberStyles.Float, CultureInfo.InvariantCulture, out number);
    private static readonly string[] StatusFields = ["quality_status", "quality_reason", "source", "source_time", "fetched_at", "is_stale", "fallback_used", "fallback_reason"];
    public static void Apply(JsonObject data, DateTimeOffset now, MarketCalendars? calendars)
    {
        var rows = (data["indices"] as JsonArray ?? []).OfType<JsonObject>().ToArray();
        var previous = (data["index_statuses"] as JsonArray ?? []).OfType<JsonObject>().ToArray();
        var items = new JsonArray(); var statuses = new JsonArray();
        foreach (var definition in IndexRegistry.All)
        {
            var status = new JsonObject { ["id"] = definition.Id, ["quality_status"] = "unavailable", ["quality_reason"] = "quote_not_returned", ["source"] = definition.Quote.Provider, ["source_time"] = null, ["fetched_at"] = null, ["is_stale"] = false, ["fallback_used"] = false, ["fallback_reason"] = null };
            var matches = rows.Where(row => Text(row, "symbol") == definition.ExchangeCode || Text(row, "id") == definition.Id).ToArray();
            var supplied = previous.FirstOrDefault(row => Text(row, "id") == definition.Id);
            if (matches.Length == 0 && supplied is not null)
            {
                // Missing rows may carry only an actual supplier failure, never an old success.
                if (Text(supplied, "quality_status") is "invalid" or "unavailable")
                    foreach (var key in StatusFields) if (supplied.ContainsKey(key)) status[key] = supplied[key]?.DeepClone();
            }
            if (matches.Length > 0)
            {
                var row = matches[0];
                var valid = matches.Length == 1 && Text(row, "symbol") == definition.ExchangeCode
                    && (Text(row, "id") is null || Text(row, "id") == definition.Id)
                    && (Text(row, "market") is null || Text(row, "market") == definition.Market)
                    && (Text(row, "currency") is null || Text(row, "currency") == definition.Currency)
                    && (Text(row, "asset_type") is null or "index")
                    && (Text(row, "source_symbol") is null || Text(row, "source_symbol") == definition.Quote.Symbol)
                    && (Text(row, "name") == definition.Name || definition.Quote.ResponseNames.Contains(Text(row, "name")))
                    && Number(row, "price", out var price) && price > 0 && Number(row, "change_rate", out _);
                var declared=Text(row,"quality_status");
                if(declared is "invalid" or "unavailable" || declared is not null && declared is not ("valid" or "stale" or "unknown_time"))
                {
                    foreach(var key in StatusFields)if(row.ContainsKey(key))status[key]=row[key]?.DeepClone();
                    status["quality_status"]=declared=="unavailable"?"unavailable":"invalid";
                    status["quality_reason"]=declared is "invalid" or "unavailable"?Text(row,"quality_reason")??"provider_rejected_observation":"unknown_quote_quality";
                }
                else if (!valid)
                { status["quality_status"] = "invalid"; status["quality_reason"] = "index_identity_or_value_mismatch"; }
                else
                {
                    row = row.DeepClone().AsObject();
                    row["id"] = definition.Id; row["name"] = definition.Name; row["asset_type"] = "index";
                    row["market"] = definition.Market; row["currency"] = definition.Currency; row["calendar_id"] = definition.CalendarId;
                    row["currency_basis"] ??= "registry_definition_and_legacy_index_identity";
                    var verified = row["trade_time_verified"] is JsonValue flag && flag.TryGetValue<bool>(out var value) && value;
                    PublicDataQuality.Stamp(row, "realtime_quote", "index", Text(row, "source_time") ?? (verified ? Text(row, "observed_at") : null), now, calendars);
                    foreach (var key in StatusFields) status[key] = row[key]?.DeepClone();
                    if (Text(row, "quality_status") != "invalid") items.Add(row);
                }
            }
            status["cache_read_at"] = now.ToString("O"); statuses.Add(status);
        }
        data["indices"] = items;
        data["registry_version"] = IndexRegistry.Version;
        data["index_registry"] = IndexRegistry.JsonDefinitions();
        data["index_statuses"] = statuses;
    }
}
