using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch2 任务7：自然语言问答后端。
/// 10 个高频问题模板（见 Templates 注释表），命中模板走确定性账本查询（不调模型、零成本，model_calls=0）；
/// 未命中才走模型链路（worker op ask.answer），受 F4 预算机制约束：预算耗尽抛 model_budget_exceeded（409，可操作提示）。
/// 模板列表（流 D 同步到 web/docs；此处为权威清单）：
///   1. year_return            今年收益       关键词：今年+收益/赚/亏
///   2. month_performance      近一月涨跌     关键词：近一月/近30天+涨跌/表现
///   3. max_drawdown_contribution 最大回撤贡献 关键词：回撤（以当前浮动亏损额近似）
///   4. position_pnl_ranking   持仓盈亏排名   关键词：持仓/持有+盈亏/排名
///   5. dca_stats              定投统计       关键词：定投
///   6. dividend_stats         分红统计       关键词：分红
///   7. fee_expenses           费率支出       关键词：费率/手续费/费用
///   8. cash_ratio             现金比例       关键词：现金+比例/占比
///   9. industry_distribution  行业分布       关键词：行业
///  10. valuation_percentile   估值分位       关键词：估值+分位/百分位（调 valuation.percentiles，确定性计算）
/// 口径声明：凡无历史快照可依赖的指标，均在 data.basis 注明近似口径，不虚构精确值。
/// 假设：worker op ask.answer 由流 B 实现（契约§2 未列出，A 端先占位透调）。
/// </summary>
public sealed class AskService(AccountDatabase database, ComputeService compute, ModelCostRepository costs)
{
    private const string Holdings = """
        SELECT p.symbol, p.asset_type, p.quantity, p.average_cost, p.market_value, i.industry, COALESCE(i.name, p.symbol) AS name
        FROM (SELECT user_id, symbol, asset_type, quantity, average_cost, market_value FROM positions WHERE user_id=$1 AND quantity>0
              UNION ALL SELECT p.user_id, p.symbol, p.asset_type, p.quantity, p.average_cost, p.market_value
              FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id
              WHERE p.user_id=$1 AND a.status='active' AND p.quantity>0) p
        LEFT JOIN instruments i ON i.user_id=p.user_id AND i.symbol=p.symbol
        """;

    public async Task<JsonObject> AskAsync(string userId, string question, CancellationToken cancellation)
    {
        var normalized = question.Replace(" ", "", StringComparison.Ordinal).Replace("　", "", StringComparison.Ordinal);
        var template = Match(normalized);
        if (template is null) return await AskModelAsync(userId, question, cancellation);
        var data = template == "valuation_percentile"
            ? await ValuationPercentileAsync(userId, cancellation)
            : await database.ReadAsync(sql => Task.FromResult(Dispatch(sql, userId, template)), cancellation);
        return new JsonObject
        {
            ["matched"] = true, ["template"] = template, ["question"] = question,
            ["answer"] = data["answer"]?.GetValue<string>(), ["data"] = data["data"],
            ["model_calls"] = 0,
        };
    }

    private static string? Match(string q)
    {
        bool Has(params string[] keys) => keys.Any(q.Contains);
        if (Has("今年") && Has("收益", "赚", "亏", "收益率")) return "year_return";
        if (Has("近一月", "近30天", "最近一月", "一个月") && Has("涨跌", "表现", "收益")) return "month_performance";
        if (Has("回撤")) return "max_drawdown_contribution";
        if ((Has("持仓", "持有") && Has("盈亏", "排名")) || Has("盈亏排名")) return "position_pnl_ranking";
        if (Has("定投")) return "dca_stats";
        if (Has("分红")) return "dividend_stats";
        if (Has("费率", "手续费", "费用支出")) return "fee_expenses";
        if (Has("现金") && Has("比例", "占比", "多少")) return "cash_ratio";
        if (Has("行业")) return "industry_distribution";
        if (Has("估值") && Has("分位", "百分位", "水位", "温度")) return "valuation_percentile";
        return null;
    }

    private async Task<JsonObject> AskModelAsync(string userId, string question, CancellationToken cancellation)
    {
        // F4 预算约束：先查预算状态，耗尽则返回可操作提示（不调模型）。
        var summary = database.Read(sql => costs.SummaryFor(sql, userId));
        if (summary["budget_blocker"] is not null)
            throw new ApplicationFailure("model_budget_exceeded", 409,
                "费用额度不足或调用费用上限未知，未调用模型。日预算将于北京时间明日 00:00 重置；" +
                "可改用更便宜的模型、调小 max tokens 后重试，或在费用设置中提高日预算。");
        var result = await compute.Run(userId, "ask.answer", new JsonObject { ["question"] = question }, cancellation, seconds: 180);
        if (result is null) throw new ApplicationFailure("worker_unavailable", 503, "问答服务暂不可用，请稍后重试。");
        return new JsonObject
        {
            ["matched"] = false, ["template"] = JsonValue.Create((string?)null), ["question"] = question,
            ["answer"] = result["answer"]?.GetValue<string>() ?? result.ToJsonString(),
            ["data"] = result, ["model_calls"] = 1,
            ["budget_warn"] = summary["budget_warn"]?.GetValue<bool>() == true,
        };
    }

    private async Task<JsonObject> ValuationPercentileAsync(string userId, CancellationToken cancellation)
    {
        var indices = QuantAssistant.Core.Market.IndexRegistry.All
            .Where(row => row.Enabled && row.Market == "CN" && row.Quote.VerificationStatus == "verified")
            .OrderBy(row => row.DisplayOrder).Take(12)
            .Select(row => (JsonNode)new JsonObject { ["symbol"] = row.ExchangeCode, ["market"] = "CN" }).ToArray();
        var result = (await compute.Run(userId, "valuation.percentiles",
            new JsonObject { ["indices"] = new JsonArray(indices), ["years"] = 10 }, cancellation, seconds: 180))?.AsObject()
            ?? throw new ApplicationFailure("worker_unavailable", 503, "估值计算未完成，请稍后重试。");
        var items = result["items"]?.AsArray() ?? new JsonArray();
        var lines = items.OfType<JsonObject>()
            .Select(i => $"{i["symbol"]?.GetValue<string>()} PE分位{i["pe_pct"]?.GetValue<double?>():0.#}%/PB分位{i["pb_pct"]?.GetValue<double?>():0.#}%");
        var temp = result["temperature"]?.AsObject();
        return Wrap($"当前估值水温：{temp?["label"]?.GetValue<string>()}（{temp?["score"]?.GetValue<double?>():0.#}分）。" + string.Join("；", lines),
            new JsonObject { ["basis"] = "valuation.percentiles（10年百分位）", ["temperature"] = temp?.DeepClone(), ["items"] = items.DeepClone() });
    }

    private static JsonObject Wrap(string answer, JsonObject data) => new() { ["answer"] = answer, ["data"] = data };

    private static JsonObject Dispatch(IAccountSql sql, string userId, string template) => template switch
    {
        "year_return" => YearReturn(sql, userId),
        "month_performance" => MonthPerformance(sql, userId),
        "max_drawdown_contribution" => DrawdownContribution(sql, userId),
        "position_pnl_ranking" => PnlRanking(sql, userId),
        "dca_stats" => DcaStats(sql, userId),
        "dividend_stats" => DividendStats(sql, userId),
        "fee_expenses" => FeeExpenses(sql, userId),
        "cash_ratio" => CashRatio(sql, userId),
        "industry_distribution" => IndustryDistribution(sql, userId),
        _ => throw new ArgumentException("未知问答模板"),
    };

    private static decimal Num(JsonObject row, string key) =>
        decimal.Parse(row[key]!.GetValue<string>(), CultureInfo.InvariantCulture);
    private static string Money(decimal value) => value.ToString("0.##", CultureInfo.InvariantCulture);
    private static DateOnly Today => DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));


    private static JsonObject YearReturn(IAccountSql sql, string userId)
    {
        var jan1 = new DateOnly(Today.Year, 1, 1);
        var holdings = sql.Query(Holdings, userId).OfType<JsonObject>().ToList();
        var total = holdings.Sum(r => Num(r, "market_value"));
        var flows = sql.Query("""
            SELECT action, COALESCE(SUM(gross_amount),0)::text AS amount FROM transactions
            WHERE user_id=$1 AND traded_at >= $2 GROUP BY action
            """, userId, jan1).OfType<JsonObject>().ToList();
        var netInvested = flows.Sum(r => r["action"]!.GetValue<string>() is "buy" or "add" ? Num(r, "amount") : -Num(r, "amount"));
        var dividends = Num(sql.Query("SELECT COALESCE(SUM(net_amount),0)::text AS amount FROM dividend_events WHERE user_id=$1 AND paid_at >= $2",
            userId, jan1).First()!.AsObject(), "amount");
        var reference = total + dividends - netInvested;
        var data = new JsonObject
        {
            ["basis"] = "账面近似口径：当前总市值+年内分红−年内净投入；未剔除年初存量持仓，精确收益需年初快照。",
            ["total_value"] = Money(total), ["net_invested_ytd"] = Money(netInvested), ["dividends_ytd"] = Money(dividends),
            ["reference_gain_ytd"] = Money(reference),
            ["reference_return_ytd"] = netInvested != 0 ? (reference / Math.Abs(netInvested)).ToString("0.##%", CultureInfo.InvariantCulture) : null,
        };
        return Wrap($"今年账面参考：年内净投入 {Money(netInvested)} 元，当前总市值 {Money(total)} 元，年内分红 {Money(dividends)} 元；" +
            $"参考收益（市值+分红−净投入）{Money(reference)} 元。口径为近似值，未剔除年初存量。", data);
    }

    private static JsonObject MonthPerformance(IAccountSql sql, string userId)
    {
        var since = Today.AddDays(-30);
        var holdings = sql.Query(Holdings, userId).OfType<JsonObject>().ToList();
        var flows = sql.Query("""
            SELECT action, COALESCE(SUM(gross_amount),0)::text AS amount, count(*)::text AS n FROM transactions
            WHERE user_id=$1 AND traded_at >= $2 GROUP BY action
            """, userId, since).OfType<JsonObject>().ToList();
        var bought = flows.Where(r => r["action"]!.GetValue<string>() is "buy" or "add").Sum(r => Num(r, "amount"));
        var sold = flows.Where(r => r["action"]!.GetValue<string>() is "sell" or "reduce").Sum(r => Num(r, "amount"));
        var data = new JsonObject
        {
            ["basis"] = "近30天成交金额统计；无逐日净值快照，涨跌以成交口径近似。",
            ["buy_amount_30d"] = Money(bought), ["sell_amount_30d"] = Money(sold), ["net_flow_30d"] = Money(bought - sold),
        };
        return Wrap($"近一月：买入 {Money(bought)} 元，卖出 {Money(sold)} 元，净流入 {Money(bought - sold)} 元。", data);
    }

    private static JsonObject DrawdownContribution(IAccountSql sql, string userId)
    {
        var rows = sql.Query(Holdings, userId).OfType<JsonObject>()
            .Select(r => new { Row = r, Pnl = Num(r, "market_value") - Num(r, "quantity") * Num(r, "average_cost") })
            .Where(x => x.Pnl < 0).OrderBy(x => x.Pnl).Take(5).ToList();
        var data = new JsonObject
        {
            ["basis"] = "以当前浮动亏损额近似回撤贡献（无历史净值曲线时的账面口径）。",
            ["contributors"] = new JsonArray(rows.Select(x => (JsonNode)new JsonObject
            {
                ["symbol"] = x.Row["symbol"]?.GetValue<string>(), ["name"] = x.Row["name"]?.GetValue<string>(),
                ["floating_pnl"] = Money(x.Pnl), ["market_value"] = Money(Num(x.Row, "market_value")),
            }).ToArray()),
        };
        var top = rows.FirstOrDefault();
        return Wrap(top is null ? "当前持仓无浮动亏损，回撤贡献为空。" :
            $"最大回撤贡献（浮动亏损额最大）：{top.Row["name"]?.GetValue<string>()}（{top.Row["symbol"]?.GetValue<string>()}），浮动盈亏 {Money(top.Pnl)} 元。", data);
    }

    private static JsonObject PnlRanking(IAccountSql sql, string userId)
    {
        var rows = sql.Query(Holdings, userId).OfType<JsonObject>()
            .Select(r =>
            {
                var cost = Num(r, "quantity") * Num(r, "average_cost");
                var pnl = Num(r, "market_value") - cost;
                return new { Row = r, Pnl = pnl, Rate = cost != 0 ? pnl / cost : (decimal?)null };
            }).OrderByDescending(x => x.Pnl).Take(10).ToList();
        var data = new JsonObject
        {
            ["basis"] = "按浮动盈亏额排序（market_value − quantity × average_cost）。",
            ["ranking"] = new JsonArray(rows.Select((x, i) => (JsonNode)new JsonObject
            {
                ["rank"] = i + 1, ["symbol"] = x.Row["symbol"]?.GetValue<string>(), ["name"] = x.Row["name"]?.GetValue<string>(),
                ["floating_pnl"] = Money(x.Pnl),
                ["pnl_rate"] = x.Rate?.ToString("0.##%", CultureInfo.InvariantCulture),
                ["market_value"] = Money(Num(x.Row, "market_value")),
            }).ToArray()),
        };
        var top = rows.FirstOrDefault();
        return Wrap(top is null ? "当前无持仓。" :
            $"持仓盈亏排名第一：{top.Row["name"]?.GetValue<string>() ?? top.Row["symbol"]?.GetValue<string>()}，浮动盈亏 {Money(top.Pnl)} 元" +
            (top.Rate is null ? "。" : $"（{top.Rate.Value.ToString("0.##%", CultureInfo.InvariantCulture)}）。"), data);
    }

    private static JsonObject DcaStats(IAccountSql sql, string userId)
    {
        var plans = sql.Query("SELECT status, count(*)::text AS n FROM dca_plans WHERE user_id=$1 GROUP BY status", userId).OfType<JsonObject>().ToList();
        var smart = sql.Query("SELECT status, count(*)::text AS n FROM smart_dca_plans WHERE user_id=$1 GROUP BY status", userId).OfType<JsonObject>().ToList();
        var tx = sql.Query("""
            SELECT count(*)::text AS n, COALESCE(SUM(gross_amount),0)::text AS amount FROM transactions
            WHERE user_id=$1 AND lower(coalesce(source,''))='dca' AND action IN('buy','add')
            """, userId).First()!.AsObject();
        var data = new JsonObject
        {
            ["basis"] = "均线版 dca_plans + 估值版 smart_dca_plans 计划状态计数；成交按 transactions.source='dca' 统计。",
            ["legacy_plans"] = new JsonArray(plans.Select(r => (JsonNode)new JsonObject { ["status"] = r["status"]?.GetValue<string>(), ["count"] = r["n"]?.GetValue<string>() }).ToArray()),
            ["smart_plans"] = new JsonArray(smart.Select(r => (JsonNode)new JsonObject { ["status"] = r["status"]?.GetValue<string>(), ["count"] = r["n"]?.GetValue<string>() }).ToArray()),
            ["dca_trades"] = tx["n"]?.GetValue<string>(), ["dca_invested"] = Money(Num(tx, "amount")),
        };
        return Wrap($"定投统计：成交 {tx["n"]?.GetValue<string>()} 笔、累计投入 {Money(Num(tx, "amount"))} 元；" +
            $"均线版计划 {plans.Sum(r => int.Parse(r["n"]!.GetValue<string>()))} 个，智能版计划 {smart.Sum(r => int.Parse(r["n"]!.GetValue<string>()))} 个。", data);
    }

    private static JsonObject DividendStats(IAccountSql sql, string userId)
    {
        var jan1 = new DateOnly(Today.Year, 1, 1);
        JsonObject Agg(string? since) => sql.Query("""
            SELECT count(*)::text AS n, COALESCE(SUM(gross_amount),0)::text AS gross,
                   COALESCE(SUM(fees),0)::text AS fees, COALESCE(SUM(net_amount),0)::text AS net
            FROM dividend_events WHERE user_id=$1 AND ($2::date IS NULL OR paid_at >= $2)
            """, userId, since).First()!.AsObject();
        var ytd = Agg(jan1.ToString("yyyy-MM-dd"));
        var all = Agg(null);
        var bySymbol = sql.Query("""
            SELECT symbol, count(*)::text AS n, COALESCE(SUM(net_amount),0)::text AS net FROM dividend_events
            WHERE user_id=$1 GROUP BY symbol ORDER BY SUM(net_amount) DESC LIMIT 5
            """, userId).OfType<JsonObject>().ToList();
        var data = new JsonObject
        {
            ["basis"] = "dividend_events 按 paid_at 统计（今年/累计）。",
            ["ytd"] = new JsonObject { ["count"] = ytd["n"]?.GetValue<string>(), ["gross"] = Money(Num(ytd, "gross")), ["fees"] = Money(Num(ytd, "fees")), ["net"] = Money(Num(ytd, "net")) },
            ["all_time"] = new JsonObject { ["count"] = all["n"]?.GetValue<string>(), ["gross"] = Money(Num(all, "gross")), ["fees"] = Money(Num(all, "fees")), ["net"] = Money(Num(all, "net")) },
            ["top_symbols"] = new JsonArray(bySymbol.Select(r => (JsonNode)new JsonObject
                { ["symbol"] = r["symbol"]?.GetValue<string>(), ["count"] = r["n"]?.GetValue<string>(), ["net"] = Money(Num(r, "net")) }).ToArray()),
        };
        return Wrap($"分红统计：今年 {ytd["n"]?.GetValue<string>()} 笔、净到账 {Money(Num(ytd, "net"))} 元；累计 {all["n"]?.GetValue<string>()} 笔、净到账 {Money(Num(all, "net"))} 元。", data);
    }

    private static JsonObject FeeExpenses(IAccountSql sql, string userId)
    {
        var jan1 = new DateOnly(Today.Year, 1, 1);
        var txFees = Num(sql.Query("SELECT COALESCE(SUM(fees),0)::text AS amount FROM transactions WHERE user_id=$1 AND traded_at >= $2",
            userId, jan1).First()!.AsObject(), "amount");
        var divFees = Num(sql.Query("SELECT COALESCE(SUM(fees),0)::text AS amount FROM dividend_events WHERE user_id=$1 AND paid_at >= $2",
            userId, jan1).First()!.AsObject(), "amount");
        var data = new JsonObject
        {
            ["basis"] = "transactions.fees + dividend_events.fees（今年以来）。",
            ["trade_fees_ytd"] = Money(txFees), ["dividend_fees_ytd"] = Money(divFees), ["total_fees_ytd"] = Money(txFees + divFees),
        };
        return Wrap($"今年费率支出合计 {Money(txFees + divFees)} 元，其中成交费用 {Money(txFees)} 元、分红费用 {Money(divFees)} 元。", data);
    }

    private static JsonObject CashRatio(IAccountSql sql, string userId)
    {
        var holdings = sql.Query(Holdings, userId).OfType<JsonObject>().ToList();
        var total = holdings.Sum(r => Num(r, "market_value"));
        var cash = holdings.Where(r => r["asset_type"]!.GetValue<string>() == "cash").Sum(r => Num(r, "market_value"));
        var ratio = total > 0 ? cash / total : 0m;
        var data = new JsonObject
        {
            ["basis"] = "positions/account_positions 中 asset_type='cash' 的市值占比。",
            ["cash_value"] = Money(cash), ["total_value"] = Money(total), ["cash_ratio"] = ratio.ToString("0.##%", CultureInfo.InvariantCulture),
        };
        return Wrap($"当前现金 {Money(cash)} 元，占总资产 {ratio.ToString("0.##%", CultureInfo.InvariantCulture)}（总资产 {Money(total)} 元）。", data);
    }

    private static JsonObject IndustryDistribution(IAccountSql sql, string userId)
    {
        var holdings = sql.Query(Holdings, userId).OfType<JsonObject>().ToList();
        var total = holdings.Sum(r => Num(r, "market_value"));
        var groups = holdings.GroupBy(r => r["industry"]?.GetValue<string>() is { Length: > 0 } name ? name : "未分类")
            .Select(g => new { Industry = g.Key, Value = g.Sum(r => Num(r, "market_value")) })
            .OrderByDescending(g => g.Value).Take(10).ToList();
        var data = new JsonObject
        {
            ["basis"] = "按 instruments.industry 汇总持仓市值占比；无行业标注的归入未分类。",
            ["total_value"] = Money(total),
            ["distribution"] = new JsonArray(groups.Select(g => (JsonNode)new JsonObject
            {
                ["industry"] = g.Industry, ["value"] = Money(g.Value),
                ["ratio"] = total > 0 ? (g.Value / total).ToString("0.##%", CultureInfo.InvariantCulture) : "0%",
            }).ToArray()),
        };
        var top = groups.FirstOrDefault();
        return Wrap(top is null ? "当前无持仓。" :
            $"行业分布：{string.Join("、", groups.Take(3).Select(g => $"{g.Industry}{(total > 0 ? (g.Value / total).ToString("0.#%", CultureInfo.InvariantCulture) : "")}"))}；" +
            $"最大行业为{top.Industry}（{Money(top.Value)} 元）。", data);
    }
}
