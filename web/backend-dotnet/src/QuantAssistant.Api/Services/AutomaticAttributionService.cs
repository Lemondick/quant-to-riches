using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using System.Text.Json;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

public sealed record AutomaticAttributionResult(string Status, string[] Blockers, AttributionReportView? View = null);

public sealed class AutomaticAttributionService(AccountDatabase database, ComputeService compute, IConfiguration configuration)
{
    private static JsonObject State(AccountSql sql, string user, string benchmark) => new()
    {
        ["financial"] = FinancialSnapshot.Read(sql, user),
        ["benchmark"] = sql.Query("SELECT * FROM benchmark_configs WHERE user_id=$1 AND benchmark_id=$2", user, benchmark),
        ["sources"] = sql.Query("SELECT * FROM data_snapshots WHERE user_id=$1 ORDER BY id", user),
        ["role"] = JsonValue.Create(sql.Scalar("SELECT role FROM users WHERE id=$1", user) as string)
    };

    public async Task<AutomaticAttributionResult> Run(string user, string portfolio, string benchmark, DateOnly start, DateOnly end, CancellationToken cancellation, bool ledger = false)
    {
        var input = database.Read(sql => new { State = State(sql, user, benchmark), Snapshot = AnalysisSnapshotRepository.Create(sql, user) });
        if (input.State["benchmark"]!.AsArray().Count != 1)
            throw new ApplicationFailure("benchmark_not_found", 404, "当前账号没有此自定义基准。");
        if (end > FundFeeRepository.Today()) return new("blocked", ["结束日期不能晚于今天，必须已有该日正式收盘价格。"]);
        var fingerprint = FinancialSnapshot.Hash(input.State);
        // Each requested report rereads filesystem evidence, even if account SQL rows are unchanged.
        var output = (await compute.Run(user, "analysis.brinson", new { calculation_id = Guid.NewGuid().ToString("N"), benchmark_id = benchmark, start_date = start, end_date = end, ledger, snapshot = input.Snapshot }, cancellation))!.AsObject();
        if (output["status"]?.GetValue<string>() == "blocked")
            return new("blocked", output["blockers"]!.AsArray().Select(b => b!.GetValue<string>()).ToArray());
        if (output["status"]?.GetValue<string>() != "ready" || output["provenance"] is not JsonObject provenance || provenance["method"]?.GetValue<string>() != (ledger ? "ledger_close_periods_v1" : "static_holdings_price_return_v1"))
            throw new ApplicationFailure("invalid_attribution_evidence", 422, "自动归因证据不完整。");
        static BrinsonHolding[] Holdings(JsonNode? rows)
        {
            if (rows is not JsonArray array) throw new ArgumentException("自动归因缺少标的数据。");
            static decimal Amount(JsonNode? value) => decimal.TryParse(value?.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var amount) ? amount : throw new ArgumentException("自动归因金额无效。");
            return array.Select(row => new BrinsonHolding(row!["symbol"]!.GetValue<string>(), row["sector_name"]!.GetValue<string>(), Amount(row["initial_value"]), Amount(row["period_return"]))).ToArray();
        }
        var periodProvenance = provenance;
        if(ledger && output["periods"] is JsonArray {Count:>1})
            periodProvenance = new JsonObject(provenance.Where(item=>item.Key is not("ledger_events" or "fund_disclosures")).Select(item=>new KeyValuePair<string,JsonNode?>(item.Key,item.Value?.DeepClone())));
        AttributionReportView Period(JsonNode row, DateOnly left, DateOnly right, string source)
        {
            var rows = BrinsonModel.Aggregate(Holdings(row["portfolio"]), Holdings(row["benchmark"]));
            var period = new AttributionReport { Id = Guid.NewGuid().ToString("N"), UserId = user, PortfolioSymbol = portfolio, BenchmarkSymbol = benchmark, StartDate = left, EndDate = right,
                Status = "completed", DataSource = source, SourceJson = periodProvenance.DeepClone().AsObject(),
                TotalActiveReturn = rows.Sum(d => d.PortfolioWeight * d.PortfolioReturn) - rows.Sum(d => d.BenchmarkWeight * d.BenchmarkReturn),
                AllocationEffect = rows.Sum(d => d.Allocation), SelectionEffect = rows.Sum(d => d.Selection), InteractionEffect = rows.Sum(d => d.Interaction) };
            foreach (var detail in rows) detail.ReportId = period.Id;
            if (ledger)
            {
                period.SourceJson["period_input"] = row.DeepClone();
                if (!decimal.TryParse(row["portfolio_return"]?.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var expected)
                    || Math.Abs(rows.Sum(d => d.PortfolioWeight * d.PortfolioReturn) - expected) > .000000000001m)
                    throw new ArgumentException("账本分期收益与行业明细不一致。");
            }
            return new(period, rows);
        }
        var periods = new List<AttributionReportView>();
        if (ledger)
        {
            if (output["periods"] is not JsonArray intervalRows || intervalRows.Count is < 1 or > 366) throw new ArgumentException("账本分期数量无效。");
            var cursor = start;
            foreach (var row in intervalRows)
            {
                if (!DateOnly.TryParseExact(row?["start_date"]?.ToString(), "yyyy-MM-dd", out var left) || left != cursor
                    || !DateOnly.TryParseExact(row?["end_date"]?.ToString(), "yyyy-MM-dd", out var right) || right <= left || right > end)
                    throw new ArgumentException("账本分期日期不连续。");
                periods.Add(Period(row!, left, right, "native_ledger_period")); cursor = right;
            }
            if (cursor != end) throw new ArgumentException("账本分期未覆盖请求区间。");
        }
        else periods.Add(Period(output, start, end, "native_static_price"));
        var report = periods[0].Report;
        var details = periods[0].Details;
        if (periods.Count > 1)
        {
            var linked = BrinsonLinking.Calculate(periods);
            report = new AttributionReport { Id = Guid.NewGuid().ToString("N"), UserId = user, PortfolioSymbol = portfolio, BenchmarkSymbol = benchmark, StartDate = start, EndDate = end,
                Status = "completed", DataSource = "native_linked_brinson", SourceJson = provenance.DeepClone().AsObject(), TotalActiveReturn = linked.ActiveReturn,
                AllocationEffect = linked.Allocation, SelectionEffect = linked.Selection, InteractionEffect = linked.Interaction };
            report.SourceJson["period_generation_method"] = report.SourceJson["method"]!.DeepClone();
            report.SourceJson["method"] = "frongello_linking_v1";
            report.SourceJson["portfolio_return"] = JsonSerializer.SerializeToNode(linked.PortfolioReturn, FinanceJson.Options);
            report.SourceJson["benchmark_return"] = JsonSerializer.SerializeToNode(linked.BenchmarkReturn, FinanceJson.Options);
            report.SourceJson["linked_sectors"] = JsonSerializer.SerializeToNode(linked.Sectors, FinanceJson.Options);
            report.SourceJson["reconciliation_residual"] = JsonSerializer.SerializeToNode(linked.ReconciliationResidual, FinanceJson.Options);
            report.SourceJson["report_ids"] = JsonSerializer.SerializeToNode(periods.Select(p => p.Report.Id).Order().ToArray());
            details = [];
        }
        // Persist server-bound inputs and safe fingerprints; private filesystem paths stay internal.
        report.SourceJson["start_date"] = start.ToString("yyyy-MM-dd");
        report.SourceJson["end_date"] = end.ToString("yyyy-MM-dd");
        report.SourceJson["benchmark_definition"] = input.State["benchmark"]![0]!.DeepClone();
        report.SourceJson["account_state_sha256"] = fingerprint;
        report.SourceJson["evidence_file_sha256"] = new JsonArray();
        var files = output["private_files"]?.AsArray() ?? throw new ArgumentException("自动归因缺少来源校验信息。");
        if (files.Count > 243) throw new ArgumentException("自动归因来源数量超限。");
        var heldFiles = new List<FileStream>();
        try
        {
        database.Write(user, sql =>
        {
            if (FinancialSnapshot.Hash(State(sql, user, benchmark)) != fingerprint)
                throw new ApplicationFailure("attribution_state_changed", 409, "计算期间账户、基准或快照已变化，请重新计算。");
            var root = Path.GetFullPath(input.Snapshot["role"]!.GetValue<string>() == "admin" ? configuration["QUANT_SITE_ROOT"]! : Path.Combine(configuration["QUANT_SITE_ROOT"]!, "users", user)) + Path.DirectorySeparatorChar;
            var comparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            foreach (var source in files.OfType<JsonObject>())
            {
                var path = Path.GetFullPath(source["path"]!.GetValue<string>());
                NativePaths.RejectReparseAncestors(Path.GetDirectoryName(path)!);
                if (!path.StartsWith(root, comparison) || !File.Exists(path) || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 || new FileInfo(path).Length > 512_000_000)
                    throw new ApplicationFailure("attribution_source_changed", 409, "来源文件不在当前账号目录内、缺失或无效，请重新计算。");
                var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                heldFiles.Add(stream);
                var hash = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
                if (hash != source["sha256"]!.GetValue<string>())
                    throw new ApplicationFailure("attribution_source_changed", 409, "计算期间来源文件发生变化，请重新计算。");
                report.SourceJson["evidence_file_sha256"]!.AsArray().Add(hash);
            }
            AttributionRepository.InsertReport(sql, report, details);
            if (periods.Count > 1)
            {
                foreach (var period in periods)
                {
                    period.Report.SourceJson.Remove("ledger_events");
                    period.Report.SourceJson["ledger_parent_report_id"] = report.Id;
                    period.Report.SourceJson["account_state_sha256"] = JsonValue.Create(fingerprint);
                    period.Report.SourceJson["evidence_file_sha256"] = report.SourceJson["evidence_file_sha256"]!.DeepClone();
                    AttributionRepository.InsertReport(sql, period.Report, period.Details);
                }
                report.SourceJson["periods"] = JsonSerializer.SerializeToNode(periods, FinanceJson.Options);
                sql.Execute("UPDATE attribution_reports SET source_json=$3::jsonb WHERE user_id=$1 AND id=$2",user,report.Id,report.SourceJson.ToJsonString());
            }
            return 0;
        });
        }
        finally { foreach (var stream in heldFiles) stream.Dispose(); }
        return new("created", [], new(report, details));
    }
}
