namespace QuantAssistant.Core.Market;

public static class MarketSessions
{
    public static string? ZoneId(string market) => market switch
    { "CN" => "Asia/Shanghai", "HK" => "Asia/Hong_Kong", "US" => "America/New_York", "JP" => "Asia/Tokyo", _ => null };
    public static DateTimeOffset? Local(DateTimeOffset instant, string market) => ZoneId(market) is { } id
        ? TimeZoneInfo.ConvertTime(instant, TimeZoneInfo.FindSystemTimeZoneById(id)) : null;

    public static string Classify(DateTimeOffset now, DateOnly day, bool? trading, string market = "CN")
    {
        var local = Local(now, market);
        if (local is null || DateOnly.FromDateTime(local.Value.DateTime) != day || trading is null) return "unknown";
        if (trading == false) return "closed";
        var rows = MarketSessionRules.For(market, day);
        if (rows is null) return "unknown";
        var minute = local.Value.TimeOfDay.TotalMinutes;
        return rows.Single(row => row.Start <= minute && minute < row.End).Phase;
    }

    public static string Classify(DateTimeOffset now, MarketCalendars calendars, string market = "CN") => Local(now, market) is { } local
        ? Classify(now, DateOnly.FromDateTime(local.DateTime), calendars.TradingDay(market, DateOnly.FromDateTime(local.DateTime)), market) : "unknown";

    public static DateOnly? Latest(DateTimeOffset now, MarketCalendars calendars, string market, bool auctionSupported)
    {
        if (Local(now, market) is not { } local) return null;
        var day = DateOnly.FromDateTime(local.DateTime);
        for (var offset = 0; offset < 371; offset++)
        {
            if (calendars.TradingDay(market, day) is not { } trading || MarketSessionRules.For(market, day) is not { } rows) return null;
            var opening = rows.Where(row => row.Phase == "continuous_trading" || auctionSupported && row.Phase == "auction").Min(row => row.Start);
            if (trading && (offset > 0 || local.TimeOfDay.TotalMinutes >= opening)) return day;
            day = day.AddDays(-1);
        }
        return null;
    }

    public static bool UnsupportedAuction(DateTimeOffset now, DateTimeOffset source, string market)
    {
        var day = DateOnly.FromDateTime(now.DateTime);
        if (day != DateOnly.FromDateTime(source.DateTime) || MarketSessionRules.For(market, day) is not { } rows) return false;
        for (var index = 0; index < rows.Count; index++)
        {
            var row = rows[index];
            if (row.Phase != "auction") continue;
            var end = index + 1 < rows.Count && rows[index + 1].Phase == "pre_market" ? rows[index + 1].End : row.End;
            if (row.Start <= now.TimeOfDay.TotalMinutes && now.TimeOfDay.TotalMinutes < end && source.TimeOfDay.TotalMinutes >= row.Start) return true;
        }
        return false;
    }

    public static DateTime Boundary(DateTimeOffset local, DateOnly session, bool auctionSupported, string market = "CN")
    {
        var rows = MarketSessionRules.For(market, session) ?? throw new ArgumentException("Session schedule unavailable");
        var close = rows.Where(row => row.Phase is "continuous_trading" or "auction").Max(row => row.End);
        var minute = local.TimeOfDay.TotalMinutes;
        if (session != DateOnly.FromDateTime(local.DateTime) || minute >= close) return session.ToDateTime(TimeOnly.MinValue).AddMinutes(close);
        var current = rows.Single(row => row.Start <= minute && minute < row.End);
        return current.Phase == "continuous_trading" || current.Phase == "auction" && auctionSupported
            ? local.DateTime : session.ToDateTime(TimeOnly.MinValue).AddMinutes(current.Start);
    }
}
