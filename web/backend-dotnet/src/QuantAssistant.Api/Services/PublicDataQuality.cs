using System.Globalization;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using QuantAssistant.Core.Market;

namespace QuantAssistant.Api.Services;

/// <summary>Read adapters for old public snapshots. Never promote a collection clock to a source clock.</summary>
public static class PublicDataQuality
{
    private static string? Text(JsonObject row, string key) => row[key] is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;
    private static readonly Regex Instant = new(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,7})?)?(?:Z|[+-]\d{2}:\d{2})$", RegexOptions.CultureInvariant);
    private static readonly Regex NoZone = new(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,7})?)?$", RegexOptions.CultureInvariant);

    public static void Stamp(JsonObject row, string type, string instrument, string? value, DateTimeOffset now, MarketCalendars? calendars = null, bool? auctionSupported = null)
    {
        calendars ??= MarketCalendars.Builtin;
        var supportsAuction = auctionSupported ?? (row["auction_supported"] is JsonValue capability && capability.TryGetValue<bool>(out var supported) && supported);
        var market = (Text(row, "market") ?? "CN").Trim().ToUpperInvariant();
        var local = MarketSessions.Local(now, market) ?? now.ToUniversalTime();
        var today = DateOnly.FromDateTime(local.DateTime);
        var quality = "unknown_time";
        string? updated = null, sourceTime = null, tradeDate = null, reason = null;
        if (value?.Length == 10 && DateOnly.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var day))
        {
            if (type is "official_nav" or "historical_quote" or "fund_profile" && day <= today)
            { updated = tradeDate = value; quality = "valid"; }
            else quality = "invalid";
        }
        else if (value is not null && value.Length <= 64 && Instant.IsMatch(value) && DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out var instant))
        {
            var source = MarketSessions.Local(instant, market) ?? instant.ToUniversalTime();
            day = DateOnly.FromDateTime(source.DateTime);
            if (instant > now.AddMinutes(1)) quality = "invalid";
            else
            {
                updated = sourceTime = value;
                tradeDate = day.ToString("yyyy-MM-dd");
                quality = "valid";
                if (type is "realtime_quote" or "intraday_estimate")
                {
                    var session = MarketSessions.Latest(now, calendars, market, supportsAuction);
                    if (session is null) { quality = "unknown_time"; reason = "calendar_or_session_unavailable"; }
                    else if (!supportsAuction && day == today &&
                        ((local.TimeOfDay >= TimeSpan.FromHours(9.25) && local.TimeOfDay < TimeSpan.FromHours(9.5) && source.TimeOfDay >= TimeSpan.FromHours(9.25)) ||
                         (local.TimeOfDay >= new TimeSpan(14, 57, 0) && local.TimeOfDay < TimeSpan.FromHours(15) && source.TimeOfDay >= new TimeSpan(14, 57, 0))))
                    { quality = "unavailable"; reason = "auction_not_supported"; }
                    else if (day < session) quality = "stale";
                    else if (day > session) quality = "invalid";
                    else
                    {
                        var boundary = MarketSessions.Boundary(local, session.Value, supportsAuction);
                        if (boundary - source.DateTime > TimeSpan.FromMinutes(type == "intraday_estimate" ? 15 : 2)) quality = "stale";
                    }
                }
            }
        }
        else if (!string.IsNullOrEmpty(value))
            quality = value.Length <= 64 && NoZone.IsMatch(value) && DateTime.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out _) ? "unknown_time" : "invalid";
        bool? navStale = null;
        if (type == "official_nav")
        {
            row["nav_age_sessions"] = null;
            row["nav_freshness_status"] = "unknown";
            if (quality != "invalid" && tradeDate is not null)
            {
                var policy = Text(row, "nav_freshness_policy") ?? NavFreshness.PolicyForFund(Text(row, "fund_type"), Text(row, "nav_publication_frequency"));
                var weekday = row["nav_valuation_weekday"] is JsonValue number && number.TryGetValue<int>(out var given) ? (int?)given : null;
                var lag = row["nav_publication_lag_sessions"] is JsonValue release && release.TryGetValue<int>(out var delay) ? (int?)delay : null;
                var nav = NavFreshness.Evaluate(DateOnly.ParseExact(tradeDate, "yyyy-MM-dd", CultureInfo.InvariantCulture), now, calendars, policy, weekday, market, lag);
                quality = nav.Status == "fresh" ? "valid" : nav.Status == "stale" ? "stale" : "unknown_time";
                reason = nav.Reason; navStale = nav.Stale;
                row["nav_freshness_policy"] = nav.Policy; row["nav_age_sessions"] = nav.Age;
                row["nav_freshness_status"] = nav.Status; row["nav_policy_basis"] = "application_config";
                row["nav_publication_lag_sessions"] = nav.PublicationLag;
            }
        }
        row["data_type"] = type; row["instrument_type"] = instrument;
        if (quality == "invalid") reason = "invalid_source_time";
        else if (quality == "unknown_time" && value is not null && NoZone.IsMatch(value)) reason = "source_timezone_missing";
        row["source_family"] = (Text(row, "source") ?? Text(row, "source_family") ?? "unknown").Replace("local:", "").Replace("akshare:", "").Replace("_fundgz", "").Replace("_latest_nav", "");
        row["source_time"] = quality == "invalid" ? null : sourceTime;
        row["updated_at"] = quality == "invalid" ? null : updated;
        row["trade_date"] = quality == "invalid" ? null : tradeDate;
        row["market"] = market;
        row["market_timezone"] = MarketSessions.ZoneId(market);
        row["market_session"] = MarketSessions.Classify(now, calendars, market);
        row["calendar_revision"] = calendars.Load(market, today.Year)?.Revision;
        row["calendar_source"] = calendars.Load(market, today.Year)?.Source;
        row["quality_reason"] = reason;
        row["auction_supported"] = supportsAuction;
        row["is_estimate"] = type == "intraday_estimate";
        row["is_stale"] = navStale ?? quality == "stale";
        row["quality_status"] = quality;
        row["fallback_used"] ??= false;
        row["fallback_reason"] ??= null;
        row["cache_read_at"] = now.ToString("O");
    }

    public static void Apply(string kind, JsonObject data, DateTimeOffset now, bool separateNav = true, MarketCalendars? calendars = null)
    {
        IEnumerable<JsonObject> Rows(string key) => (data[key] as JsonArray ?? []).OfType<JsonObject>();
        void Quotes(IEnumerable<JsonObject> rows)
        {
            foreach (var row in rows)
            {
                var verified = row["trade_time_verified"]?.GetValue<bool>() == true;
                Stamp(row, "realtime_quote", Text(row, "instrument_type") ?? Text(row, "asset_type") ?? "stock", Text(row, "source_time") ?? (verified ? Text(row, "observed_at") : null), now, calendars);
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
                    Stamp(row, "official_nav", "fund", Text(row, "nav_date"), now, calendars);
                }
                else Stamp(row, "intraday_estimate", "fund", Text(row, "source_time") ?? Text(row, "observed_at"), now, calendars);
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
            foreach (var row in Rows("fund_rankings")) Stamp(row, "official_nav", "fund", Text(row, "nav_date"), now, calendars);
            foreach (var row in Rows("sector_rankings").Concat(Rows("sector_catalogs")))
            {
                Stamp(row, "realtime_quote", "sector", Text(row, "source_time") ?? Text(row, "observed_at"), now, calendars);
                if (row.ContainsKey("code")) row["standard_id"] = $"{Text(row,"source")}:{Text(row,"category")}:{Text(row,"code")}";
                if (row.ContainsKey("change_rate") && row["change_rate"] is null) row["quality_status"] = "unavailable";
            }
            if (data["breadth"] is JsonObject breadth) Stamp(breadth, "historical_quote", "market_breadth", Text(breadth, "updated_at") ?? Text(breadth, "observed_at")?[..10], now, calendars);
        }
        if (kind == "fund.detail")
        {
            Stamp(data, "fund_profile", "fund", Text(data, "source_time"), now, calendars);
            data["nav_updated_at"] = Rows("nav_history").Select(row => Text(row, "nav_date")).Max();
            var latest = Rows("nav_history").OrderBy(row => Text(row, "nav_date"), StringComparer.Ordinal).LastOrDefault();
            data["nav_metadata"] ??= new JsonObject();
            if (data["nav_metadata"] is JsonObject nav)
            {
                nav["fund_type"] ??= data["fund_type"]?.DeepClone();
                nav["source"] ??= latest?["source"]?.DeepClone();
                Stamp(nav, "official_nav", "fund", Text(data, "nav_updated_at"), now, calendars);
            }
            foreach (var row in Rows("period_returns").Where(row => !row.ContainsKey("methodology")))
            { row["return_rate"] = null; row["quality_status"] = "unavailable"; row["reason"] = "legacy_return_unverified"; }
        }
        if (kind == "market.history") Stamp(data, "historical_quote", "index", Rows("items").Select(row => Text(row, "date")).Max(), now, calendars);
        data["cache_read_at"] = now.ToString("O");
    }
}
