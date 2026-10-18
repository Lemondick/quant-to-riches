using QuantAssistant.Core.Errors;

namespace QuantAssistant.Core.Portfolio;

public sealed record RedemptionTier(int MinDays, int? MaxDays, decimal Rate);
public sealed record PurchaseTier(decimal MinAmount, decimal? MaxAmount, decimal? Rate = null, decimal? FixedAmount = null);
public sealed record ServiceRefundRule(DateOnly EffectiveFrom, int AfterHoldingDays, string EvidenceReference);
public sealed record FundFeeSchedule(string Symbol, string ShareClass, decimal PurchaseRate, decimal AnnualServiceRate,
    RedemptionTier[] RedemptionTiers, DateOnly VerifiedOn, DateOnly EffectiveFrom, DateOnly? EffectiveTo = null,
    PurchaseTier[]? PurchaseTiers = null, string Channel = "alipay", string EvidenceReference = "", ServiceRefundRule? ServiceRefundRule = null);
public sealed record FundCostEstimate(decimal PurchaseFee, decimal RedemptionFee, decimal ServiceFeeEstimate, decimal ExpectedServiceRefund, decimal NetCostEstimate, decimal CashCredit = 0);

public static class FundFeeRules
{
    public static FundFeeSchedule Validate(FundFeeSchedule value, DateOnly today)
    {
        if (value.ShareClass is not ("A" or "C") || value.Channel != "alipay" || string.IsNullOrWhiteSpace(value.EvidenceReference)
            || value.EvidenceReference.Length > 1000 || value.VerifiedOn > today || value.EffectiveTo < value.EffectiveFrom
            || value.PurchaseRate is < 0 or > 1 || value.AnnualServiceRate is < 0 or > 1 || value.RedemptionTiers.Length is < 1 or > 30
            || value.PurchaseTiers?.Length > 30) throw new ArgumentException("基金费率证据无效");
        var redemption = value.RedemptionTiers.OrderBy(t => t.MinDays).ToArray();
        if (redemption[0].MinDays != 0 || redemption[^1].MaxDays is not null
            || redemption.Any(t => t.MinDays < 0 || t.MaxDays < t.MinDays || t.Rate is < 0 or > 1)
            || redemption.Zip(redemption.Skip(1)).Any(p => p.First.MaxDays is null || (long)p.First.MaxDays + 1 != p.Second.MinDays))
            throw new ArgumentException("赎回费档须连续覆盖全部持有天数");
        var purchase = (value.PurchaseTiers ?? []).OrderBy(t => t.MinAmount).ToArray();
        if (purchase.Length > 0 && (purchase[0].MinAmount != 0 || purchase[^1].MaxAmount is not null
            || purchase.Any(t => t.MinAmount < 0 || t.MaxAmount <= t.MinAmount || (t.Rate is null) == (t.FixedAmount is null)
                || t.Rate is < 0 or > 1 || t.FixedAmount < 0)
            || purchase.Zip(purchase.Skip(1)).Any(p => p.First.MaxAmount != p.Second.MinAmount)))
            throw new ArgumentException("申购费档须连续覆盖全部金额");
        if (value.ServiceRefundRule is { } rule && (rule.AfterHoldingDays is < 0 or > 36600
            || string.IsNullOrWhiteSpace(rule.EvidenceReference) || rule.EvidenceReference.Length > 1000))
            throw new ArgumentException("服务费返还规则无效");
        return value with { RedemptionTiers = redemption, PurchaseTiers = purchase, EvidenceReference = value.EvidenceReference.Trim() };
    }

    public static void Verify(FundFeeSchedule schedule, DateOnly asOf)
    {
        if (schedule.VerifiedOn > asOf || asOf.DayNumber - schedule.VerifiedOn.DayNumber > 30
            || asOf < schedule.EffectiveFrom || asOf > schedule.EffectiveTo) throw new UnverifiedFeeScheduleError();
    }

    public static decimal RedemptionRate(FundFeeSchedule schedule, int days)
    {
        if (days < 0) throw new ArgumentException("赎回确认日早于买入确认日");
        var tier = schedule.RedemptionTiers.SingleOrDefault(t => t.MinDays <= days && (t.MaxDays is null || days <= t.MaxDays));
        return tier?.Rate ?? throw new UnverifiedFeeScheduleError();
    }

    public static FundCostEstimate Quote(FundFeeSchedule schedule, decimal amount, int holdingDays, DateOnly? confirmedOn, bool navIncludesServiceFee)
    {
        if (amount <= 0 || amount >= 100000000000000000000m || holdingDays is < 0 or > 36600) throw new ArgumentException("费用估算参数无效");
        var purchaseRate = schedule.PurchaseRate; decimal? fixedAmount = null;
        if (schedule.PurchaseTiers is { Length: > 0 } tiers)
        {
            var tier = tiers.Single(t => t.MinAmount <= amount && (t.MaxAmount is null || amount < t.MaxAmount));
            purchaseRate = tier.Rate ?? 0; fixedAmount = tier.FixedAmount;
        }
        var purchase = fixedAmount ?? amount - amount / (1 + purchaseRate);
        if (purchase > amount) throw new ArgumentException("申购费用超过申购金额");
        purchase = Round(purchase);
        var redemption = amount * RedemptionRate(schedule, holdingDays);
        var service = amount * schedule.AnnualServiceRate * holdingDays / 365;
        var refund = 0m;
        if (schedule.ServiceRefundRule is { } rule && schedule.AnnualServiceRate > 0)
        {
            if (confirmedOn is null) throw new UnverifiedFeeScheduleError();
            var eligible = confirmedOn.Value.AddDays(rule.AfterHoldingDays);
            if (eligible < rule.EffectiveFrom) eligible = rule.EffectiveFrom;
            var refundDays = Math.Max(0, confirmedOn.Value.AddDays(holdingDays).DayNumber - eligible.DayNumber);
            refund = amount * schedule.AnnualServiceRate * refundDays / 365;
        }
        return new(purchase, Round(redemption), Round(service), Round(refund), Round(purchase + redemption + (navIncludesServiceFee ? 0 : service - refund)));
    }

    public static decimal Round(decimal value) => decimal.Round(value, 2, MidpointRounding.AwayFromZero);
}
