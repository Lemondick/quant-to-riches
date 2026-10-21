using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch3 流 A：慢查询聚合告警。
/// 每小时整点过后评估上一个完整小时桶：某 endpoint 慢请求（>500ms）次数 ≥
/// QUANT_SLOW_QUERY_ALERT_MIN_COUNT（默认 20）且 p95 ≥ QUANT_SLOW_QUERY_ALERT_P95_MS
/// （默认 2000）时，向管理者写 critical 告警（operational_alerts，alert_key 含小时桶，
/// 天然按小时去重；2 小时前的旧告警自动 resolve）。
/// critical 告警经邮件通道（EmailDeliveryService.CheckDispatch）自动发邮件。
/// 由 Scheduler Tick 驱动（CheckHourly，小时门控保证每小时只评估一次）。
/// </summary>
public sealed class SlowQueryAlertService(
    AccountDatabase database, SlowRequestAggregator aggregator, QuantOptions options, ILogger<SlowQueryAlertService> logger)
{
    private string _lastEvaluatedHour = "";

    public void CheckHourly()
    {
        var now = DateTime.UtcNow;
        var completedHour = SlowRequestAggregator.HourKey(now.AddHours(-1));
        if (_lastEvaluatedHour == completedHour) return;
        _lastEvaluatedHour = completedHour;
        try
        {
            var stats = aggregator.SnapshotHour(completedHour);
            var minCount = options.SlowQueryAlertMinCount;
            var p95Ms = options.SlowQueryAlertP95Ms;
            var breached = stats.Where(s => s.Count >= minCount && s.P95Ms >= p95Ms).ToList();
            var admins = database.Read(sql => sql.Query("SELECT id FROM users WHERE status='active' AND role='admin'"))
                .OfType<JsonObject>().Select(a => a["id"]!.GetValue<string>()).ToArray();
            foreach (var adminId in admins)
            {
                database.WriteNonLedger(adminId, sql =>
                {
                    foreach (var b in breached)
                    {
                        var key = $"slow_query:{completedHour}:{b.Endpoint}";
                        var detail = new JsonObject
                        {
                            ["endpoint"] = b.Endpoint, ["hour"] = completedHour, ["count"] = b.Count,
                            ["p95_ms"] = Math.Round(b.P95Ms, 1),
                            ["threshold_count"] = minCount, ["threshold_p95_ms"] = p95Ms,
                        };
                        sql.Execute("""
                            INSERT INTO operational_alerts(user_id,alert_key,severity,message,status,first_seen_at,last_seen_at,details_json)
                            VALUES($1,$2,'critical',$3,'open',now(),now(),$4::json)
                            ON CONFLICT(user_id,alert_key,status) DO UPDATE SET message=EXCLUDED.message,last_seen_at=EXCLUDED.last_seen_at,details_json=EXCLUDED.details_json
                            """, adminId, key,
                            $"接口 {b.Endpoint} 在 {completedHour[..8]}日{completedHour[8..]}时 慢请求 {b.Count} 次（>500ms），p95={b.P95Ms:0}ms，" +
                            $"超过阈值（{minCount}次/p95 {p95Ms}ms），请检查性能。",
                            detail.ToJsonString());
                    }
                    // 2 小时前的旧 slow_query 告警自动 resolve（alert_key 以小时开头，可按字典序比较）。
                    sql.Execute("UPDATE operational_alerts SET status='resolved',last_seen_at=now() WHERE user_id=$1 AND status='open' AND alert_key LIKE 'slow_query:%' AND alert_key < $2",
                        adminId, "slow_query:" + SlowRequestAggregator.HourKey(now.AddHours(-2)));
                    return 0;
                });
            }
            if (breached.Count > 0)
                logger.LogWarning("慢查询聚合告警：{Hour} 时段 {Count} 个接口超阈值", completedHour, breached.Count);
            aggregator.Prune(completedHour);
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            logger.LogWarning("慢查询聚合告警评估失败：{ErrorType}", error.GetType().Name);
        }
    }
}
