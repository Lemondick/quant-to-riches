using System.Globalization;
using System.Text.Json.Nodes;

namespace QuantAssistant.Api.Services;

/// <summary>Read adapters for old public snapshots. Never promote a collection clock to a source clock.</summary>
public static class PublicDataQuality
{
    private static string? Text(JsonObject row, string key) => row[key] is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;
    private static bool Trading(DateOnly day) => day.DayOfWeek is not (DayOfWeek.Saturday or DayOfWeek.Sunday) &&
        !new[] { ("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"), ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"), ("10-01", "10-07") }
        .Any(range => string.CompareOrdinal(day.ToString("MM-dd"), range.Item1) >= 0 && string.CompareOrdinal(day.ToString("MM-dd"), range.Item2) <= 0);

    public static void Stamp(JsonObject row, string type, string instrument, string? value, DateTimeOffset now)
    {
        var local = now.ToOffset(TimeSpan.FromHours(8));
        var today = DateOnly.FromDateTime(local.DateTime);
        var quality = "unknown_time";
        string? updated = null, sourceTime = null, tradeDate = null;
        if (value?.Length == 10 && DateOnly.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var day))
        {
            if (type is "official_nav" or "historical_quote" or "fund_profile" && day <= today)
            { updated = tradeDate = value; quality = type == "official_nav" && today.DayNumber - day.DayNumber > 14 ? "stale" : "valid"; }
            else quality = "invalid";
        }
        else if (value is not null && (value.EndsWith('Z') || value.Contains('+')) && DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out var instant))
        {
            var source = instant.ToOffset(TimeSpan.FromHours(8));
            day = DateOnly.FromDateTime(source.DateTime);
            if (instant > now.AddMinutes(1)) quality = "invalid";
            else
            {
                updated = sourceTime = value;
                tradeDate = day.ToString("yyyy-MM-dd");
                quality = "valid";
                if (type is "realtime_quote" or "intraday_estimate")
                {
                    if (today.Year != 2026) quality = "unknown_time";
                    else
                    {
                        var session = local.TimeOfDay < TimeSpan.FromHours(9.5) ? today.AddDays(-1) : today;
                        while (!Trading(session)) session = session.AddDays(-1);
                        if (day < session) quality = "stale";
                        else if (day > session) quality = "invalid";
                        else if (Trading(today) && local.TimeOfDay >= TimeSpan.FromHours(9.5))
                        {
                            var boundary = local.TimeOfDay > TimeSpan.FromHours(15) ? local.Date.AddHours(15) : local.DateTime;
                            if (local.TimeOfDay >= TimeSpan.FromHours(11.5) && local.TimeOfDay < TimeSpan.FromHours(13)) boundary = local.Date.AddHours(11.5);
                            if (boundary - source.DateTime > TimeSpan.FromMinutes(type == "intraday_estimate" ? 15 : 2)) quality = "stale";
                        }
                    }
                }
            }
        }
        row["data_type"] = type; row["instrument_type"] = instrument;
        row["source_family"] = (Text(row, "source") ?? "unknown").Replace("local:", "").Replace("akshare:", "").Replace("_fundgz", "").Replace("_latest_nav", "");
        row["source_time"] = quality == "invalid" ? null : sourceTime;
        row["updated_at"] = quality == "invalid" ? null : updated;
        row["trade_date"] = tradeDate;
        row["is_estimate"] = type == "intraday_estimate";
        row["is_stale"] = quality == "stale";
        row["quality_status"] = quality;
        row["fallback_used"] ??= false;
        row["fallback_reason"] ??= null;
        row["cache_read_at"] = now.ToString("O");
    }

    public static void Apply(string kind, JsonObject data, DateTimeOffset now, bool separateNav = true)
    {
        IEnumerable<JsonObject> Rows(string key) => (data[key] as JsonArray ?? []).OfType<JsonObject>();
        void Quotes(IEnumerable<JsonObject> rows)
        {
            foreach (var row in rows)
            {
                var verified = row["trade_time_verified"]?.GetValue<bool>() == true;
                Stamp(row, "realtime_quote", Text(row, "instrument_type") ?? Text(row, "asset_type") ?? "stock", Text(row, "source_time") ?? (verified ? Text(row, "observed_at") : null), now);
            }
        }
        if (kind == "market.quotes") Quotes(Rows("items"));
        if (kind == "fund.valuations")
        {
            foreach (var row in Rows("items"))
            {
                if (Text(row, "valuation_type") == "latest_nav")
                {
                    row["official_nav"] ??= row["estimate_nav"]?.DeepClone();
                    row["official_change_rate"] ??= row["change_rate"]?.DeepClone();
                    row["estimate_nav"] = row["change_rate"] = row["change_amount"] = row["observed_at"] = null;
                    Stamp(row, "official_nav", "fund", Text(row, "nav_date"), now);
                }
                else Stamp(row, "intraday_estimate", "fund", Text(row, "source_time") ?? Text(row, "observed_at"), now);
            }
            if (!separateNav)
            {
                var omitted = Rows("items").Where(row => row["estimate_nav"] is null).ToArray();
                data["items"] = new JsonArray(Rows("items").Where(row => row["estimate_nav"] is not null).Select(row => row.DeepClone()).ToArray());
                var unavailable = (data["unavailable"] as JsonArray ?? []).Select(node => node?.GetValue<string>()).Concat(omitted.Select(row => Text(row, "symbol"))).Where(value => value is not null).Distinct();
                data["unavailable"] = new JsonArray(unavailable.Select(value => (JsonNode?)JsonValue.Create(value)).ToArray());
            }
        }
        if (kind == "market.snapshot")
        {
            Quotes(Rows("indices"));
            foreach (var row in Rows("fund_rankings")) Stamp(row, "official_nav", "fund", Text(row, "nav_date"), now);
            foreach (var row in Rows("sector_rankings").Concat(Rows("sector_catalogs")))
            {
                Stamp(row, "realtime_quote", "sector", Text(row, "source_time") ?? Text(row, "observed_at"), now);
                if (row.ContainsKey("code")) row["standard_id"] = $"{Text(row,"source")}:{Text(row,"category")}:{Text(row,"code")}";
                if (row.ContainsKey("change_rate") && row["change_rate"] is null) row["quality_status"] = "unavailable";
            }
            if (data["breadth"] is JsonObject breadth) Stamp(breadth, "historical_quote", "market_breadth", Text(breadth, "updated_at") ?? Text(breadth, "observed_at")?[..10], now);
        }
        if (kind == "fund.detail")
        {
            Stamp(data, "fund_profile", "fund", Text(data, "source_time"), now);
            data["nav_updated_at"] = Rows("nav_history").Select(row => Text(row, "nav_date")).Max();
            foreach (var row in Rows("period_returns").Where(row => !row.ContainsKey("methodology")))
            { row["return_rate"] = null; row["quality_status"] = "unavailable"; row["reason"] = "legacy_return_unverified"; }
        }
        if (kind == "market.history") Stamp(data, "historical_quote", "index", Rows("items").Select(row => Text(row, "date")).Max(), now);
        data["cache_read_at"] = now.ToString("O");
    }
}
