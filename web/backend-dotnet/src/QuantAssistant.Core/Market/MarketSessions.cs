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
        // Overseas session schedules require a verified market contract; never use the CN schedule.
        if (market != "CN") return "unknown";
        var time = local.Value.TimeOfDay;
        if (time < TimeSpan.FromHours(9.25)) return "pre_market";
        if (time < new TimeSpan(9, 25, 0)) return "auction";
        if (time < TimeSpan.FromHours(9.5)) return "pre_market";
        if (time < TimeSpan.FromHours(11.5)) return "continuous_trading";
        if (time < TimeSpan.FromHours(13)) return "lunch_break";
        if (time < new TimeSpan(14, 57, 0)) return "continuous_trading";
        return time < TimeSpan.FromHours(15) ? "auction" : "closed";
    }

    public static string Classify(DateTimeOffset now, MarketCalendars calendars, string market = "CN") => Local(now, market) is { } local
        ? Classify(now, DateOnly.FromDateTime(local.DateTime), calendars.TradingDay(market, DateOnly.FromDateTime(local.DateTime)), market) : "unknown";

    public static DateOnly? Latest(DateTimeOffset now, MarketCalendars calendars, string market, bool auctionSupported)
    {
        if (market != "CN" || Local(now, market) is not { } local) return null;
        var day = DateOnly.FromDateTime(local.DateTime);
        if (calendars.TradingDay(market, day) is not { } trading) return null;
        if (trading && local.TimeOfDay >= TimeSpan.FromHours(auctionSupported ? 9.25 : 9.5)) return day;
        for (var index = 0; index < 370; index++)
        {
            day = day.AddDays(-1);
            if (calendars.TradingDay(market, day) is not { } previous) return null;
            if (previous) return day;
        }
        return null;
    }

    public static DateTime Boundary(DateTimeOffset local, DateOnly session, bool auctionSupported)
    {
        if (session != DateOnly.FromDateTime(local.DateTime) || local.TimeOfDay >= TimeSpan.FromHours(15))
            return session.ToDateTime(new TimeOnly(15, 0));
        if (auctionSupported && local.TimeOfDay < TimeSpan.FromHours(9.5))
            return local.TimeOfDay < new TimeSpan(9, 25, 0) ? local.DateTime : local.Date.AddHours(9).AddMinutes(25);
        if (local.TimeOfDay >= TimeSpan.FromHours(11.5) && local.TimeOfDay < TimeSpan.FromHours(13)) return local.Date.AddHours(11.5);
        return local.DateTime;
    }
}
