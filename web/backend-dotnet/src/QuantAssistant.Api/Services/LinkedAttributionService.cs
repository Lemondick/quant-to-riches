using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

public sealed class LinkedAttributionService(AccountDatabase database, IAttributionRepository repository)
{
    public AttributionReportView Create(string user, string reportId, IReadOnlyList<string> reportIds)
    {
        if (!Guid.TryParseExact(reportId, "N", out _) || reportIds.Count is < 2 or > 60
            || reportIds.Any(id => !Guid.TryParseExact(id, "N", out _)) || reportIds.Distinct(StringComparer.OrdinalIgnoreCase).Count() != reportIds.Count)
            throw new ArgumentException("请选择2至60份不同的有效报告。");
        reportId = reportId.ToLowerInvariant();
        var ids = reportIds.Select(id => id.ToLowerInvariant()).Order(StringComparer.Ordinal).ToArray();
        database.WriteNonLedger(user, sql =>
        {
            // Serialize same-ID requests across accounts as well as within an account.
            sql.Execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "attribution:" + reportId);
            var existing = sql.Query("SELECT user_id,data_source,source_json FROM attribution_reports WHERE id=$1", reportId).FirstOrDefault();
            if (existing is not null)
            {
                if (existing["user_id"]!.GetValue<string>() != user || existing["data_source"]!.GetValue<string>() != "native_linked_brinson"
                    || !JsonNode.DeepEquals(existing["source_json"]!["report_ids"], JsonSerializer.SerializeToNode(ids)))
                    throw new ApplicationFailure("attribution_link_conflict", 409, "此报告编号已用于其他内容，请重新读取并核对。");
                return 0;
            }
            var periods = new List<AttributionReportView>();
            foreach (var id in ids)
            {
                var row = sql.Query("SELECT * FROM attribution_reports WHERE user_id=$1 AND id=$2 FOR SHARE", user, id).FirstOrDefault()
                    ?? throw new ApplicationFailure("report_not_found", 404, "所选报告不存在或不属于当前账号。");
                try
                {
                    var report = row.Deserialize<AttributionReport>(FinanceJson.Options)!;
                    var details = sql.Query("SELECT * FROM attribution_details WHERE report_id=$1 ORDER BY sector_name FOR SHARE", id)
                        .Select(d => d!.Deserialize<AttributionDetail>(FinanceJson.Options)!).ToList();
                    periods.Add(new(report, details));
                }
                catch (JsonException) { throw new ArgumentException("单期行业权重或收益证据不完整，请重新核对原报告。"); }
            }
            periods = periods.OrderBy(p => p.Report.StartDate).ThenBy(p => p.Report.EndDate).ToList();
            var linked = BrinsonLinking.Calculate(periods);
            var source = new JsonObject
            {
                ["method"] = "frongello_linking_v1", ["report_ids"] = JsonSerializer.SerializeToNode(ids),
                ["periods"] = JsonSerializer.SerializeToNode(periods, FinanceJson.Options),
                ["portfolio_return"] = JsonSerializer.SerializeToNode(linked.PortfolioReturn, FinanceJson.Options),
                ["benchmark_return"] = JsonSerializer.SerializeToNode(linked.BenchmarkReturn, FinanceJson.Options),
                ["linked_sectors"] = JsonSerializer.SerializeToNode(linked.Sectors, FinanceJson.Options),
                ["reconciliation_residual"] = JsonSerializer.SerializeToNode(linked.ReconciliationResidual, FinanceJson.Options),
                ["limitations"] = new JsonArray("按日期顺序使用Frongello递推；每期保留自己的期初权重和收益。",
                    "仅连结所选已保存报告；手工输入、固定持仓价格收益和原来源的限制继续有效。",
                    "不自动重建成交、现金流或基金穿透；有外部资金流时各单期收益须先正确中和资金流。")
            };
            var combined = new AttributionReport { Id = reportId, UserId = user, PortfolioSymbol = periods[0].Report.PortfolioSymbol,
                BenchmarkSymbol = periods[0].Report.BenchmarkSymbol, StartDate = periods[0].Report.StartDate, EndDate = periods[^1].Report.EndDate,
                Status = "completed", DataSource = "native_linked_brinson", SourceJson = source, TotalActiveReturn = linked.ActiveReturn,
                AllocationEffect = linked.Allocation, SelectionEffect = linked.Selection, InteractionEffect = linked.Interaction };
            // Cumulative sector effects have no single start weight. Keep them in
            // provenance rather than fabricate single-period attribution details.
            AttributionRepository.InsertReport(sql, combined, null);
            return 0;
        });
        return repository.GetReportView(user, reportId)!;
    }
}
