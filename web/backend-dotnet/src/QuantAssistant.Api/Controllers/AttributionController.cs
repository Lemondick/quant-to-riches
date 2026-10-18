using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Portfolio;
using System.Globalization;
using System.Text;
using System.Text.Json;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

[ApiController]
public class AttributionController(IAttributionRepository attrRepo, AutomaticAttributionService automatic, LinkedAttributionService linked, AccountDatabase database) : ControllerBase
{
    public sealed record LinkAttributionBody(string ReportId, List<string> ReportIds);

    [HttpPost("~/api/v1/attribution/reports/link")]
    public IActionResult LinkReports([FromBody] LinkAttributionBody body)
    {
        var view = linked.Create(CurrentUserId(), body.ReportId, body.ReportIds);
        return Created($"/api/v1/attribution/reports/{view.Report.Id}", view);
    }

    private string CurrentUserId()
    {
        return User.FindFirst("sub")?.Value ?? throw new QuantAssistant.Core.Auth.AuthFailure("authentication_required");
    }

    [HttpPost("~/api/v1/attribution/reports/automatic")]
    public async Task<IActionResult> CreateAutomaticReport([FromBody] AutomaticAttributionBody body, CancellationToken cancellation)
    {
        if (!DateOnly.TryParseExact(body.StartDate, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var start) || !DateOnly.TryParseExact(body.EndDate, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var end) || start > end)
            return BadRequest(new { error_code = "invalid_date", message = "日期格式错误" });
        if (!ValidName(body.PortfolioSymbol, 64) || !ValidName(body.BenchmarkId, 128)) return InvalidInput("请填写组合标识并选择当前账号的自定义基准。");
        var result = await automatic.Run(CurrentUserId(), body.PortfolioSymbol.Trim(), body.BenchmarkId.Trim(), start, end, cancellation, body.Ledger);
        return result.View is null ? Ok(result) : Created($"/api/v1/attribution/reports/{result.View.Report.Id}", result);
    }

    [HttpPost("~/api/v1/attribution/reports")]
    public IActionResult CreateReport([FromBody] CreateAttributionBody body)
    {
        if (!DateOnly.TryParseExact(body.StartDate, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var start) || !DateOnly.TryParseExact(body.EndDate, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var end) || start > end)
            return BadRequest(new { error_code = "invalid_date", message = "日期格式错误" });
        if (!ValidName(body.PortfolioSymbol, 64) || !ValidName(body.BenchmarkSymbol, 64))
            return InvalidInput("请填写有效的组合和基准标识（最多64字）。");
        if (body.Details is not null && ValidateDetails(body.Details) is { } failure) return InvalidInput(failure);

        var report = new AttributionReport
        {
            Id = Guid.NewGuid().ToString("N"),
            UserId = CurrentUserId(),
            PortfolioSymbol = body.PortfolioSymbol.Trim(),
            BenchmarkSymbol = body.BenchmarkSymbol.Trim(),
            StartDate = start,
            EndDate = end,
            Status = "draft"
        };
        
        if (body.Details is not null) Calculate(report, body.Details);
        attrRepo.CreateReport(report, body.Details);
        return Created($"/api/v1/attribution/reports/{report.Id}", report);
    }

    [HttpGet("~/api/v1/attribution/reports")]
    public IActionResult ListReports([FromQuery] int limit = 20, [FromQuery] int offset = 0,
        [FromQuery] string query = "", [FromQuery] string source = "", [FromQuery] string from = "", [FromQuery] string to = "")
    {
        if (limit is < 1 or > 100 || offset is < 0 or > 100000)
            return BadRequest(new { error_code = "invalid_pagination", message = "报告条数须为1至100，偏移须为0至100000。" });
        var user = CurrentUserId();
        if (query.Length > 128 || query.Any(char.IsControl) || source is not ("" or "manual_sector_input" or "native_static_price" or "native_ledger_period" or "native_linked_brinson"))
            return InvalidInput("报告筛选条件无效。");
        DateOnly? first = null, last = null;
        if (from != "") { if (!DateOnly.TryParseExact(from, "yyyy-MM-dd", out var date)) return InvalidInput("筛选日期无效。"); first = date; }
        if (to != "") { if (!DateOnly.TryParseExact(to, "yyyy-MM-dd", out var date)) return InvalidInput("筛选日期无效。"); last = date; }
        if (first > last) return InvalidInput("筛选开始日期不能晚于结束日期。");
        const string filter = "user_id=$1 AND ($2='' OR strpos(lower(portfolio_symbol||' '||benchmark_symbol||' '||id),lower($2))>0) AND ($3='' OR data_source=$3) AND ($4::date IS NULL OR start_date>=$4) AND ($5::date IS NULL OR end_date<=$5)";
        return Ok(database.Read(sql =>
        {
            var total = Convert.ToInt64(sql.Scalar("SELECT count(*) FROM attribution_reports WHERE " + filter, user, query.Trim(), source, first, last));
            var items = sql.Query("""
                SELECT id,portfolio_symbol,benchmark_symbol,start_date,end_date,status,data_source,
                    total_active_return,created_at,updated_at
                FROM attribution_reports WHERE
                """ + " " + filter + " ORDER BY created_at DESC,id DESC LIMIT $6 OFFSET $7", user, query.Trim(), source, first, last, limit, offset);
            return new { total, items, has_more = offset + items.Count < total };
        }));
    }

    [HttpGet("~/api/v1/attribution/reports/{report_id}/export")]
    public IActionResult ExportReport(string report_id, [FromQuery] string format = "json")
    {
        if (format is not ("csv" or "json")) return InvalidInput("导出格式须为csv或json。");
        var view = attrRepo.GetReportView(CurrentUserId(), report_id);
        if (view is null) return NotFound(new { error_code = "report_not_found", message = "归因报告不存在" });
        if (view.Report.Status != "completed") return Conflict(new { error_code = "report_incomplete", message = "请先完成报告计算。" });
        var filename = "attribution-" + (Guid.TryParseExact(view.Report.Id, "N", out _) ? view.Report.Id : "report");
        if (format == "json") return File(JsonSerializer.SerializeToUtf8Bytes(view, FinanceJson.Options), "application/json; charset=utf-8", filename + ".json");
        static string Cell(object? value)
        {
            var text = Convert.ToString(value, CultureInfo.InvariantCulture) ?? "";
            if (text.TrimStart().StartsWith('=') || text.TrimStart().StartsWith('+') || text.TrimStart().StartsWith('-') || text.TrimStart().StartsWith('@')) text = "'" + text;
            return "\"" + text.Replace("\"", "\"\"") + "\"";
        }
        var csv = new StringBuilder("\ufeffreport_id,portfolio,benchmark,start,end,source,sector,portfolio_weight,benchmark_weight,portfolio_return,benchmark_return,allocation,selection,interaction,total_active_return\r\n");
        void Row(string sector, object? weight, object? benchmarkWeight, object? returns, object? benchmarkReturns, object? allocation, object? selection, object? interaction) =>
            csv.AppendLine(string.Join(',', new object?[] { view.Report.Id, view.Report.PortfolioSymbol, view.Report.BenchmarkSymbol, view.Report.StartDate.ToString("yyyy-MM-dd"), view.Report.EndDate.ToString("yyyy-MM-dd"), view.Report.DataSource, sector, weight, benchmarkWeight, returns, benchmarkReturns, allocation, selection, interaction, view.Report.TotalActiveReturn }.Select(Cell)));
        foreach (var detail in view.Details) Row(detail.SectorName, detail.PortfolioWeight, detail.BenchmarkWeight, detail.PortfolioReturn, detail.BenchmarkReturn, detail.Allocation, detail.Selection, detail.Interaction);
        if (view.Report.SourceJson["linked_sectors"] is System.Text.Json.Nodes.JsonArray sectors)
            foreach (var sector in sectors) Row(sector!["sector_name"]!.ToString(), null, null, null, null, sector["allocation"]?.ToString(), sector["selection"]?.ToString(), sector["interaction"]?.ToString());
        return File(Encoding.UTF8.GetBytes(csv.ToString()), "text/csv; charset=utf-8", filename + ".csv");
    }

    [HttpGet("~/api/v1/attribution/reports/{report_id}")]
    public IActionResult GetReport(string report_id)
    {
        var view = attrRepo.GetReportView(CurrentUserId(), report_id);
        if (view is null)
            return NotFound(new { error_code = "report_not_found", message = "归因报告不存在" });
        return Ok(view);
    }

    [HttpPost("~/api/v1/attribution/reports/{report_id}/calculate")]
    public IActionResult CalculateEffects(string report_id, [FromBody] CalculateAttributionBody body)
    {
        var report = attrRepo.GetReport(CurrentUserId(), report_id);
        if (report is null)
            return NotFound(new { error_code = "report_not_found", message = "归因报告不存在" });

        if (report.DataSource is "native_linked_brinson" or "native_ledger_period")
            return Conflict(new { error_code = "attribution_link_immutable", message = "多期报告的来源已冻结，不能按单期重新计算。请重新选择单期来源生成新报告。" });

        if (ValidateDetails(body.Details) is { } failure) return InvalidInput(failure);
        Calculate(report, body.Details);
        if (!attrRepo.CompleteReport(CurrentUserId(), report, body.Details))
            return NotFound(new { error_code = "report_not_found", message = "归因报告不存在" });

        return Ok(new
        {
            total_active_return = report.TotalActiveReturn,
            allocation_effect = report.AllocationEffect,
            selection_effect = report.SelectionEffect,
            interaction_effect = report.InteractionEffect
        });
    }

    private IActionResult InvalidInput(string message) => BadRequest(new { error_code = "invalid_attribution_input", message });
    private static bool ValidName(string? name, int max) => !string.IsNullOrWhiteSpace(name) && name.Trim().Length <= max && !name.Any(char.IsControl);
    private static string? ValidateDetails(List<AttributionDetail>? details)
    {
        if (details is null || details.Count is < 1 or > 200) return "请提供1至200个行业的完整数据。";
        if (details.Any(d => d is null || !ValidName(d.SectorName, 80))) return "行业名称不能为空，且最多80字。";
        foreach (var detail in details) detail.SectorName = detail.SectorName.Trim();
        if (details.Select(d => d.SectorName).Distinct(StringComparer.OrdinalIgnoreCase).Count() != details.Count) return "行业名称不能重复。";
        if (details.Any(d => d.PortfolioWeight is < 0 or > 1 || d.BenchmarkWeight is < 0 or > 1)) return "行业权重须为0至1的比例。";
        if (details.Sum(d => d.PortfolioWeight) != 1m || details.Sum(d => d.BenchmarkWeight) != 1m) return "组合和基准的行业权重合计都必须为100%。";
        if (details.Any(d => d.PortfolioReturn is < -1 or > 1000000 || d.BenchmarkReturn is < -1 or > 1000000)) return "行业收益率须不低于-100%，并处于支持的范围内。";
        return null;
    }
    private static void Calculate(AttributionReport report, List<AttributionDetail> details)
    {
        foreach (var detail in details) detail.ReportId = report.Id;
        BrinsonModel.CalculateEffects(details);
        report.TotalActiveReturn = details.Sum(d => d.PortfolioWeight * d.PortfolioReturn) - details.Sum(d => d.BenchmarkWeight * d.BenchmarkReturn);
        report.AllocationEffect = details.Sum(d => d.Allocation);
        report.SelectionEffect = details.Sum(d => d.Selection);
        report.InteractionEffect = details.Sum(d => d.Interaction);
        report.Status = "completed";
    }
}

public class CreateAttributionBody
{
    public string PortfolioSymbol { get; set; } = "";
    public string BenchmarkSymbol { get; set; } = "";
    public string StartDate { get; set; } = "";
    public string EndDate { get; set; } = "";
    public List<AttributionDetail>? Details { get; set; }
}

public class CalculateAttributionBody
{
    public List<AttributionDetail> Details { get; set; } = new();
}

public sealed record AutomaticAttributionBody(string PortfolioSymbol, string BenchmarkId, string StartDate, string EndDate, bool Ledger = false);
