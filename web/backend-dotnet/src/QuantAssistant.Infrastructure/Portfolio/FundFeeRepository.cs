using QuantAssistant.Core.Abstractions;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Portfolio;

public sealed partial class FundFeeRepository(AccountDatabase database)
{
    [GeneratedRegex(@"(?<!\d)(\d{6})(?:\.(?:OF|SH|SZ|BJ)|[AC])?(?!\d)", RegexOptions.IgnoreCase)]
    private static partial Regex FundPattern();
    public static string Symbol(string value)
    {
        var match = FundPattern().Match(value.Trim());
        return match.Success ? match.Groups[1].Value : throw new ArgumentException("基金代码必须包含六位数字");
    }
    public static DateOnly Today() => DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
    private static decimal Amount(JsonObject row, string key) => decimal.Parse(row[key]!.GetValue<string>(), CultureInfo.InvariantCulture);
    private static FundFeeSchedule Decode(JsonObject row)
    {
        var terms = row["terms_json"]!.AsObject();
        if (terms["version"] is not null && terms["version"]!.GetValue<int>() != 1) throw new ArgumentException("费率证据版本无效");
        return new(row["symbol"]!.GetValue<string>(), terms["share_class"]!.GetValue<string>(), Amount(row, "purchase_fee_rate"), Amount(row, "service_fee_rate_annual"),
            row["redemption_tiers_json"]!.Deserialize<RedemptionTier[]>(FinanceJson.Options)!,
            DateOnly.Parse(row["verified_on"]!.GetValue<string>()), DateOnly.Parse(row["effective_from"]!.GetValue<string>()),
            row["effective_to"] is null ? null : DateOnly.Parse(row["effective_to"]!.GetValue<string>()),
            terms["purchase_tiers"]?.Deserialize<PurchaseTier[]>(FinanceJson.Options) ?? [], terms["channel"]!.GetValue<string>(),
            terms["evidence_reference"]!.GetValue<string>(), terms["service_refund_rule"]?.Deserialize<ServiceRefundRule>(FinanceJson.Options));
    }
    private static FundFeeSchedule[] Schedules(IAccountSql sql, string userId, string symbol) => sql.Query("SELECT symbol,effective_from,effective_to,purchase_fee_rate,service_fee_rate_annual,redemption_tiers_json,verified_on,terms_json FROM fund_fee_schedules WHERE user_id=$1 AND symbol=$2 ORDER BY effective_from DESC,id DESC", userId, symbol)
        .OfType<JsonObject>().Where(r => r["terms_json"]?["channel"]?.GetValue<string>() == "alipay" && !string.IsNullOrWhiteSpace(r["terms_json"]?["evidence_reference"]?.GetValue<string>())).Select(Decode).ToArray();

    public JsonObject List(string userId, string symbol) => database.Read(sql => View(sql, userId, Symbol(symbol)));
    private static JsonObject View(IAccountSql sql, string userId, string symbol)
    {
        var currentSelected = false; var items = new JsonArray();
        foreach (var schedule in Schedules(sql, userId, symbol))
        {
            var usable = !currentSelected && schedule.EffectiveFrom <= Today();
            if (usable) currentSelected = true;
            try { FundFeeRules.Verify(schedule, Today()); } catch (UnverifiedFeeScheduleError) { usable = false; }
            var item = JsonSerializer.SerializeToNode(schedule, FinanceJson.Options)!.AsObject(); item["usable_today"] = usable; items.Add(item);
        }
        return new() { ["symbol"] = symbol, ["items"] = items };
    }

    public JsonObject Save(string userId, FundFeeSchedule schedule)
    {
        schedule = FundFeeRules.Validate(schedule, Today());
        if (Symbol(schedule.Symbol) != schedule.Symbol || decimal.Round(schedule.PurchaseRate, 10) != schedule.PurchaseRate || decimal.Round(schedule.AnnualServiceRate, 10) != schedule.AnnualServiceRate)
            throw new ArgumentException("费率代码或小数精度无效");
        return database.WriteNonLedger(userId, sql =>
        {
            var before = sql.Query("SELECT effective_from,effective_to,purchase_fee_rate,service_fee_rate_annual,redemption_tiers_json,terms_json FROM fund_fee_schedules WHERE user_id=$1 AND symbol=$2 AND effective_from=$3", userId, schedule.Symbol, schedule.EffectiveFrom).FirstOrDefault();
            var terms = JsonSerializer.Serialize(new { version = 1, share_class = schedule.ShareClass, purchase_tiers = schedule.PurchaseTiers,
                channel = schedule.Channel, evidence_reference = schedule.EvidenceReference, service_refund_rule = schedule.ServiceRefundRule }, FinanceJson.Options);
            sql.Execute("""
                INSERT INTO fund_fee_schedules(user_id,symbol,effective_from,effective_to,purchase_fee_rate,service_fee_rate_annual,redemption_tiers_json,source,verified_on,terms_json)
                VALUES($1,$2,$3,$4,$5,$6,$7::json,$8,$9,$10::json) ON CONFLICT(user_id,symbol,effective_from) DO UPDATE SET
                effective_to=EXCLUDED.effective_to,purchase_fee_rate=EXCLUDED.purchase_fee_rate,service_fee_rate_annual=EXCLUDED.service_fee_rate_annual,
                redemption_tiers_json=EXCLUDED.redemption_tiers_json,source=EXCLUDED.source,verified_on=EXCLUDED.verified_on,terms_json=EXCLUDED.terms_json
                """, userId, schedule.Symbol, schedule.EffectiveFrom, schedule.EffectiveTo, schedule.PurchaseRate, schedule.AnnualServiceRate,
                JsonSerializer.Serialize(schedule.RedemptionTiers, FinanceJson.Options), schedule.EvidenceReference, schedule.VerifiedOn, terms);
            sql.Execute("INSERT INTO audit_log(user_id,entity_type,entity_id,changed_at,before_json,after_json) VALUES($1,'fund_fee_evidence',$2,now(),$3::json,$4::json)", userId, schedule.Symbol, before?.ToJsonString(), JsonSerializer.Serialize(schedule, FinanceJson.Options));
            return View(sql, userId, schedule.Symbol);
        });
    }

    public FundFeeSchedule Current(IAccountSql sql, string userId, string symbol, DateOnly asOf)
    {
        // Select the latest effective schedule, then verify it. Never fall back to superseded fees.
        var selected = Schedules(sql, userId, Symbol(symbol)).FirstOrDefault(s => s.EffectiveFrom <= Today()) ?? throw new UnverifiedFeeScheduleError();
        FundFeeRules.Verify(selected, Today()); FundFeeRules.Verify(selected, asOf); return selected;
    }

    public JsonObject Quote(string userId, string symbol, decimal amount, int days, DateOnly? confirmedOn, bool navIncludesFee) => database.Read(sql =>
    {
        var schedule = Current(sql, userId, symbol, Today());
        var result = JsonSerializer.SerializeToNode(FundFeeRules.Quote(schedule, amount, days, confirmedOn, navIncludesFee), FinanceJson.Options)!.AsObject();
        result["symbol"] = schedule.Symbol; result["estimated_on"] = Today().ToString("yyyy-MM-dd"); result["basis"] = "constant_capital_planning";
        result["evidence_reference"] = schedule.EvidenceReference; result["research_only"] = true; return result;
    });

    public JsonObject RedemptionQuote(string userId, string symbol, (DateOnly ConfirmedOn, decimal GrossAmount)[] lots, DateOnly confirmedOn) => database.Read(sql =>
    {
        if (lots.Length is < 1 or > 100 || lots.Any(l => l.GrossAmount <= 0 || l.GrossAmount >= 100000000000000000000m)) throw new ArgumentException("赎回批次无效");
        var schedule = Current(sql, userId, symbol, Today()); var items = new JsonArray(); var total = 0m;
        foreach (var lot in lots)
        {
            var days = confirmedOn.DayNumber - lot.ConfirmedOn.DayNumber; var rate = FundFeeRules.RedemptionRate(schedule, days);
            var fee = FundFeeRules.Round(lot.GrossAmount * rate); total += fee;
            items.Add(JsonSerializer.SerializeToNode(new { confirmed_on = lot.ConfirmedOn, holding_days = days, gross_amount = lot.GrossAmount, rate, fee }, FinanceJson.Options));
        }
        return new JsonObject { ["symbol"] = schedule.Symbol, ["items"] = items, ["total_fee"] = total.ToString(CultureInfo.InvariantCulture), ["cash_credit"] = "0", ["research_only"] = true };
    });
}
