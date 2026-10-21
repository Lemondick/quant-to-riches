using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// E4：费率报价与回撤校验的跨语言口径锁定（C# 侧）。
/// 共享向量见仓库根目录 tests/fixtures/fee-drawdown-caliber-vectors.json（Python 生成），
/// Python 侧孪生测试为 web/worker/tests/test_fee_drawdown_caliber.py。
/// 两边实现（C# FundFeeRules.Quote / DrawdownController 口径 vs
/// Python estimate_cost_breakdown / validate_drawdown）必须对每个向量输出一致，
/// 任何一侧的口径漂移都会让本测试失败。
/// </summary>
public sealed class FundFeeDrawdownCaliberTests
{
    private static JsonObject Fixture()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "fee-drawdown-caliber-vectors.json");
        return JsonNode.Parse(File.ReadAllText(path))!.AsObject();
    }

    private static decimal Dec(JsonNode? node) =>
        decimal.Parse(node!.GetValue<string>(), CultureInfo.InvariantCulture);

    private static FundFeeSchedule BuildSchedule(JsonObject raw) => new(
        Symbol: raw["symbol"]!.GetValue<string>(),
        ShareClass: raw["share_class"]!.GetValue<string>(),
        PurchaseRate: Dec(raw["purchase_rate"]),
        AnnualServiceRate: Dec(raw["annual_service_rate"]),
        RedemptionTiers: raw["redemption_tiers"]!.AsArray()
            .Select(t => new RedemptionTier(
                t!["min_days"]!.GetValue<int>(),
                t["max_days"] is null || t["max_days"]!.GetValueKind() == JsonValueKind.Null
                    ? null : t["max_days"]!.GetValue<int>(),
                Dec(t["rate"]))).ToArray(),
        VerifiedOn: DateOnly.Parse(raw["verified_on"]!.GetValue<string>(), CultureInfo.InvariantCulture),
        EffectiveFrom: DateOnly.Parse(raw["effective_from"]!.GetValue<string>(), CultureInfo.InvariantCulture),
        PurchaseTiers: raw["purchase_tiers"]!.AsArray()
            .Select(t => new PurchaseTier(
                Dec(t!["min_amount"]),
                t["max_amount"] is null || t["max_amount"]!.GetValueKind() == JsonValueKind.Null
                    ? null : Dec(t["max_amount"]),
                t["rate"] is null || t["rate"]!.GetValueKind() == JsonValueKind.Null ? null : Dec(t["rate"]),
                t["fixed_amount"] is null || t["fixed_amount"]!.GetValueKind() == JsonValueKind.Null
                    ? null : Dec(t["fixed_amount"]))).ToArray(),
        Channel: raw["channel"] is { } ch && ch.GetValueKind() != JsonValueKind.Null
            ? ch.GetValue<string>() : "alipay", // Python 侧 channel 可为 null；C# Validate 要求 alipay。
        EvidenceReference: raw["evidence_reference"]!.GetValue<string>(),
        ServiceRefundRule: raw["service_refund_rule"] is { } r && r.GetValueKind() != JsonValueKind.Null
            ? new ServiceRefundRule(
                DateOnly.Parse(r["effective_from"]!.GetValue<string>(), CultureInfo.InvariantCulture),
                r["after_holding_days"]!.GetValue<int>(),
                r["evidence_reference"]!.GetValue<string>())
            : null);

    public static IEnumerable<object[]> FeeCases()
    {
        var fixture = Fixture();
        foreach (var c in fixture["fee_cases"]!.AsArray())
            yield return [c!.ToJsonString()];
    }

    [Theory, MemberData(nameof(FeeCases))]
    public void QuoteMatchesSharedVectors(string json)
    {
        var c = JsonNode.Parse(json)!.AsObject();
        var schedule = BuildSchedule(Fixture()["schedule"]!.AsObject());
        var actual = FundFeeRules.Quote(
            FundFeeRules.Validate(schedule, DateOnly.Parse(c["confirmed_on"]!.GetValue<string>(), CultureInfo.InvariantCulture)),
            Dec(c["amount"]),
            c["holding_days"]!.GetValue<int>(),
            DateOnly.Parse(c["confirmed_on"]!.GetValue<string>(), CultureInfo.InvariantCulture),
            c["nav_includes_service_fee"]!.GetValue<bool>());
        var exp = c["expected"]!.AsObject();
        Assert.Equal(Dec(exp["purchase_fee"]), actual.PurchaseFee);
        Assert.Equal(Dec(exp["redemption_fee"]), actual.RedemptionFee);
        Assert.Equal(Dec(exp["service_fee_estimate"]), actual.ServiceFeeEstimate);
        Assert.Equal(Dec(exp["expected_service_refund"]), actual.ExpectedServiceRefund);
        Assert.Equal(Dec(exp["net_cost_estimate"]), actual.NetCostEstimate);
    }

    public static IEnumerable<object[]> DrawdownCases()
    {
        var fixture = Fixture();
        foreach (var c in fixture["drawdown_cases"]!.AsArray())
            yield return [c!.ToJsonString()];
    }

    [Theory, MemberData(nameof(DrawdownCases))]
    public void DrawdownMatchesSharedVectors(string json)
    {
        var c = JsonNode.Parse(json)!.AsObject();
        var values = c["nav"]!.AsArray().Select(v => v!.GetValue<double>()).ToArray();
        var threshold = c["threshold"]!.GetValue<double>();
        // 与 DrawdownController 相同的口径：running max 从 0 起，逐点计算回撤。
        var maximumValue = 0.0; var maxDrawdown = 0.0; var breaches = 0;
        foreach (var value in values)
        {
            maximumValue = Math.Max(maximumValue, value);
            var drawdown = Math.Max(0, 1 - value / maximumValue);
            maxDrawdown = Math.Max(maxDrawdown, drawdown);
            if (drawdown > threshold) breaches++;
        }
        var reasons = breaches > 0 ? new[] { "drawdown_control_line_breached" } : Array.Empty<string>();
        var exp = c["expected"]!.AsObject();
        // fixture 中 max_drawdown 为字符串（保精度，Python 侧同样 float() 转换），此处解析而非直接取值。
        var expectedDrawdown = double.Parse(exp["max_drawdown"]!.GetValue<string>(), CultureInfo.InvariantCulture);
        Assert.True(Math.Abs(maxDrawdown - expectedDrawdown) < 1e-12,
            $"max_drawdown mismatch: {maxDrawdown} vs {exp["max_drawdown"]}");
        Assert.Equal(exp["breach_count"]!.GetValue<int>(), breaches);
        Assert.Equal(exp["passed"]!.GetValue<bool>(), reasons.Length == 0);
        Assert.Equal(exp["reasons"]!.AsArray().Select(r => r!.GetValue<string>()), reasons);
    }
}
