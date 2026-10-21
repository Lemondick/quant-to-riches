using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Research;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/research/agent-runs")]
public sealed class ResearchController(ResearchExecutionService research, ResearchJobQueryRepository jobs) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet]
    public async Task<IActionResult> List(int limit = 30, CancellationToken cancellation = default)
    {
        if (limit is < 1 or > 100) throw new ArgumentException("研究任务条数无效");
        return Ok(new { items = await jobs.ListJobsAsync(Id, limit, cancellation) });
    }

    [HttpGet("~/api/v1/research/previews")]
    public async Task<IActionResult> Previews(CancellationToken cancellation) =>
        Ok(new { items = await jobs.ListPreviewsAsync(Id, cancellation) });

    [HttpPost]
    public async Task<IActionResult> Create(CreateResearchBody body, CancellationToken cancellation)
    { var (result, reused) = await research.Create(Id, body.PreviewSnapshotId, body.Symbol, cancellation); return reused ? Ok(result) : Accepted(result); }

    [HttpGet("{job_id}")] public IActionResult Get(string job_id) => Ok(research.Get(Id, job_id));
    [HttpPost("{job_id}/cancel")] public IActionResult Cancel(string job_id) => Ok(research.Cancel(Id, job_id));

    [HttpPost("{job_id}/retry")]
    public async Task<IActionResult> Retry(string job_id, RetryResearchBody body, CancellationToken cancellation)
    { var old = research.Get(Id, job_id); var (result, reused) = await research.Create(Id, body.PreviewSnapshotId, old["symbol"]!.GetValue<string>(), cancellation, job_id); return reused ? Ok(result) : Accepted(result); }

    [HttpPost("~/api/v1/research/screener")]
    public IActionResult Screener([FromServices] IFactorRepository factors, ScreenerRequestBody body)
    {
        if (!DateOnly.TryParse(body.DataDate, out var day) || body.TopN is < 1 or > 100 || body.Weights.Count is < 1 or > 100 || body.Weights.Any(p => p.Value < 0) || body.Weights.Values.Sum() != 1) throw new ArgumentException("筛选参数无效");
        var definitions = factors.ListDefinitions(Id).ToDictionary(d => d.FactorCode, d => d.Direction);
        var results = factors.GetCrossSection(Id, day).Where(e => body.Weights.ContainsKey(e.FactorCode) && e.ZScore.HasValue).GroupBy(e => e.Symbol)
            .Select(group => new { symbol = group.Key, total_score = Math.Round(group.Sum(e => body.Weights[e.FactorCode] * e.ZScore!.Value * definitions.GetValueOrDefault(e.FactorCode, 1)), 4), factors = group.ToDictionary(e => e.FactorCode, e => Math.Round(body.Weights[e.FactorCode] * e.ZScore!.Value * definitions.GetValueOrDefault(e.FactorCode, 1), 4)) })
            .OrderByDescending(item => item.total_score).ThenBy(item => item.symbol).Take(body.TopN);
        return Ok(new { data_date = body.DataDate, results });
    }
}

public sealed class CreateResearchBody { public string PreviewSnapshotId { get; set; } = ""; public string Symbol { get; set; } = ""; }
public sealed class RetryResearchBody { public string PreviewSnapshotId { get; set; } = ""; }
public sealed class ScreenerRequestBody { public string DataDate { get; set; } = ""; public Dictionary<string, decimal> Weights { get; set; } = []; public int TopN { get; set; } = 20; }
