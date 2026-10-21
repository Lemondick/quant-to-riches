using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>
/// batch2 任务1：组合再平衡目标配置与偏离计算。
/// 目标配置（rebalance_targets，每用户一条）：股/债/现金目标比例、行业集中度上限、偏离阈值。
/// 偏离计算按当前持仓市值算实际比例；超阈值生成"卖出X买入Y"调仓建议——只给建议，绝不下单。
/// 口径假设（commit 注明）：股=stock+index，债=fund，现金=cash。
/// 偏离超阈值或行业超限时写入 user_alert_center（kind=rebalance_drift，F3 critical 告警模式的用户侧对应）。
/// </summary>
public sealed class RebalanceRepository(IAccountDatabase database)
{
    private const string Columns = "stock_ratio,bond_ratio,cash_ratio,industry_concentration_limit,deviation_threshold,updated_at";

    public async Task<JsonObject?> GetAsync(string userId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql =>
            (await sql.QueryAsync($"SELECT {Columns} FROM rebalance_targets WHERE user_id=$1", userId))
                .FirstOrDefault()?.AsObject() is { } row ? View(row) : null, cancellation);

    public async Task<JsonObject> SaveAsync(string userId, decimal stock, decimal bond, decimal cash,
        decimal industryLimit, decimal deviationThreshold, CancellationToken cancellation = default)
    {
        Validate(stock, bond, cash, industryLimit, deviationThreshold);
        return await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var rows = await sql.QueryAsync($"""
                INSERT INTO rebalance_targets(user_id,stock_ratio,bond_ratio,cash_ratio,industry_concentration_limit,deviation_threshold,updated_at)
                VALUES($1,$2,$3,$4,$5,$6,now())
                ON CONFLICT(user_id) DO UPDATE SET stock_ratio=EXCLUDED.stock_ratio,bond_ratio=EXCLUDED.bond_ratio,
                    cash_ratio=EXCLUDED.cash_ratio,industry_concentration_limit=EXCLUDED.industry_concentration_limit,
                    deviation_threshold=EXCLUDED.deviation_threshold,updated_at=now()
                RETURNING {Columns}
                """, userId, stock, bond, cash, industryLimit, deviationThreshold);
            return View(rows[0]!.AsObject());
        }, cancellation);
    }

    private static void Validate(decimal stock, decimal bond, decimal cash, decimal industryLimit, decimal deviationThreshold)
    {
        if (stock is < 0 or > 1 || bond is < 0 or > 1 || cash is < 0 or > 1)
            throw new ArgumentException("目标比例须在 0 到 1 之间。");
        if (Math.Abs(stock + bond + cash - 1m) > 0.0001m)
            throw new ArgumentException("股/债/现金目标比例之和必须为 1（100%）。");
        if (industryLimit is <= 0 or > 1)
            throw new ArgumentException("行业集中度上限须在 0 到 1 之间。");
        if (deviationThreshold is <= 0 or > 0.5m)
            throw new ArgumentException("偏离阈值须在 0 到 0.5 之间。");
    }

    private static JsonObject View(JsonObject row) => new()
    {
        ["stock_ratio"] = DecimalText(row, "stock_ratio"), ["bond_ratio"] = DecimalText(row, "bond_ratio"),
        ["cash_ratio"] = DecimalText(row, "cash_ratio"),
        ["industry_concentration_limit"] = DecimalText(row, "industry_concentration_limit"),
        ["deviation_threshold"] = DecimalText(row, "deviation_threshold"),
        ["updated_at"] = row["updated_at"]?.GetValue<string>(),
    };

    private static string? DecimalText(JsonObject row, string key) =>
        row[key] is null ? null : decimal.Parse(row[key]!.GetValue<string>(), CultureInfo.InvariantCulture).ToString("0.####", CultureInfo.InvariantCulture);

    /// <summary>
    /// 偏离评估：按当前持仓市值算实际股/债/现金比例，对比目标；超阈值生成调仓建议并写告警。
    /// 无目标配置时抛 rebalance_target_not_found（404）。
    /// </summary>
    public async Task<JsonObject> EvaluateAsync(string userId, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId, async sql =>
        {
            var target = (await sql.QueryAsync($"SELECT {Columns} FROM rebalance_targets WHERE user_id=$1", userId))
                .FirstOrDefault()?.AsObject()
                ?? throw new ApplicationFailure(ErrorCodes.RebalanceTargetNotFound, 404, ErrorMessages.Get(ErrorCodes.RebalanceTargetNotFound));

            // 当前持仓市值：本地主账本 positions + 活跃子账户 account_positions（与 AlertCenterRepository 同口径）。
            var rows = await sql.QueryAsync("""
                SELECT p.asset_type, p.market_value, i.industry
                FROM (SELECT user_id, symbol, asset_type, market_value FROM positions WHERE user_id=$1 AND quantity>0
                      UNION ALL SELECT p.user_id, p.symbol, p.asset_type, p.market_value
                      FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id
                      WHERE p.user_id=$1 AND a.status='active' AND p.quantity>0) p
                LEFT JOIN instruments i ON i.user_id=p.user_id AND i.symbol=p.symbol
                """, userId);

            var values = new Dictionary<string, decimal> { ["stock"] = 0m, ["bond"] = 0m, ["cash"] = 0m };
            var industries = new Dictionary<string, decimal>(StringComparer.Ordinal);
            var total = 0m;
            foreach (var row in rows.OfType<JsonObject>())
            {
                var value = decimal.Parse(row["market_value"]!.GetValue<string>(), CultureInfo.InvariantCulture);
                if (value <= 0) continue;
                total += value;
                var bucket = row["asset_type"]!.GetValue<string>() switch
                {
                    "stock" or "index" => "stock",   // 口径假设：指数类多为股票指数，计入股。
                    "fund" => "bond",               // 口径假设：基金持仓计入债类（未做穿透）。
                    _ => "cash",                    // cash 及其他计入现金。
                };
                values[bucket] += value;
                if (row["industry"]?.GetValue<string>() is { Length: > 0 } industry)
                    industries[industry] = industries.GetValueOrDefault(industry) + value;
            }

            var deviationThreshold = decimal.Parse(target["deviation_threshold"]!.GetValue<string>(), CultureInfo.InvariantCulture);
            var industryLimit = decimal.Parse(target["industry_concentration_limit"]!.GetValue<string>(), CultureInfo.InvariantCulture);
            var targets = new Dictionary<string, decimal>
            {
                ["stock"] = decimal.Parse(target["stock_ratio"]!.GetValue<string>(), CultureInfo.InvariantCulture),
                ["bond"] = decimal.Parse(target["bond_ratio"]!.GetValue<string>(), CultureInfo.InvariantCulture),
                ["cash"] = decimal.Parse(target["cash_ratio"]!.GetValue<string>(), CultureInfo.InvariantCulture),
            };

            var actual = values.ToDictionary(p => p.Key, p => total > 0 ? p.Value / total : 0m);
            var drifts = new JsonArray();
            var suggestions = new JsonArray();
            var breaches = new List<string>();
            if (total > 0)
            {
                foreach (var bucket in new[] { "stock", "bond", "cash" })
                {
                    var drift = actual[bucket] - targets[bucket];
                    var abs = Math.Abs(drift);
                    var item = new JsonObject
                    {
                        ["asset_class"] = bucket, ["target"] = targets[bucket].ToString("0.####", CultureInfo.InvariantCulture),
                        ["actual"] = actual[bucket].ToString("0.####", CultureInfo.InvariantCulture),
                        ["drift"] = drift.ToString("0.####", CultureInfo.InvariantCulture), ["breached"] = abs > deviationThreshold,
                    };
                    drifts.Add(item);
                    if (abs > deviationThreshold) breaches.Add(bucket);
                }
                // 只给"卖出X买入Y"方案：超配类卖出、低配类买入，金额为偏离值的一半（逐步调仓，避免一次性全调）。
                // 绝不下单——返回的只是建议数据。
                var over = breaches.Where(b => actual[b] > targets[b]).ToList();
                var under = breaches.Where(b => actual[b] < targets[b]).ToList();
                foreach (var sell in over)
                foreach (var buy in under)
                {
                    var amount = Math.Round(Math.Min(Math.Abs(actual[sell] - targets[sell]), Math.Abs(actual[buy] - targets[buy])) * total / 2m, 2);
                    if (amount <= 0) continue;
                    suggestions.Add(new JsonObject
                    {
                        ["action"] = "sell", ["asset_class"] = sell, ["amount"] = amount.ToString("0.##", CultureInfo.InvariantCulture),
                        ["note"] = $"卖出{Label(sell)}类资产约 {amount} 元",
                    });
                    suggestions.Add(new JsonObject
                    {
                        ["action"] = "buy", ["asset_class"] = buy, ["amount"] = amount.ToString("0.##", CultureInfo.InvariantCulture),
                        ["note"] = $"买入{Label(buy)}类资产约 {amount} 元",
                    });
                }
            }

            // 行业集中度：有行业标注的持仓按行业汇总，对比上限。
            var topIndustry = industries.OrderByDescending(p => p.Value).FirstOrDefault();
            var industryBreach = total > 0 && topIndustry.Value / total > industryLimit ? topIndustry.Key : null;

            var hasBreach = breaches.Count > 0 || industryBreach is not null;
            // 偏离告警：接入现有告警机制（user_alert_center，F3 critical 模式的用户侧对应）。
            await sql.ExecuteAsync("UPDATE user_alert_center SET active=false WHERE user_id=$1 AND kind='rebalance_drift'", userId);
            if (hasBreach)
            {
                var detail = new JsonObject
                {
                    ["severity"] = "critical",
                    ["message"] = $"组合偏离目标：{string.Join("、", breaches.Select(Label))}" + (industryBreach is null ? "" : $"；{industryBreach}行业集中度超限"),
                    ["breaches"] = new JsonArray(breaches.Select(b => (JsonNode)JsonValue.Create(b)!).ToArray()),
                    ["industry_breach"] = industryBreach,
                    ["suggestions"] = suggestions.DeepClone(),
                };
                await sql.ExecuteAsync("""
                    INSERT INTO user_alert_center(user_id,id,kind,subject,detail,first_seen,last_seen,active,acknowledged)
                    VALUES($1,$2,'rebalance_drift','portfolio',$3::json,now(),now(),true,false)
                    ON CONFLICT(user_id,kind,subject) DO UPDATE SET detail=EXCLUDED.detail,last_seen=now(),active=true,acknowledged=false
                    """, userId, Guid.NewGuid().ToString("N"), detail.ToJsonString());
            }

            return new JsonObject
            {
                ["as_of"] = DateTimeOffset.UtcNow.ToString("O"),
                ["total_value"] = total.ToString("0.##", CultureInfo.InvariantCulture),
                ["allocation"] = new JsonObject { ["stock"] = values["stock"].ToString("0.##", CultureInfo.InvariantCulture),
                    ["bond"] = values["bond"].ToString("0.##", CultureInfo.InvariantCulture), ["cash"] = values["cash"].ToString("0.##", CultureInfo.InvariantCulture) },
                ["targets"] = View(target),
                ["drifts"] = drifts,
                ["industry"] = new JsonObject { ["top"] = topIndustry.Key, ["ratio"] = total > 0 ? (topIndustry.Value / total).ToString("0.####", CultureInfo.InvariantCulture) : "0", ["limit"] = industryLimit.ToString("0.####", CultureInfo.InvariantCulture), ["breached"] = industryBreach is not null },
                ["suggestions"] = suggestions,
                ["breached"] = hasBreach,
                ["alert_raised"] = hasBreach,
                ["research_only"] = true, ["automatic_trading"] = false,
            };
        }, cancellation);

    private static string Label(string bucket) => bucket switch { "stock" => "股票", "bond" => "债券", "cash" => "现金", _ => bucket };
}
