using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1")]
public sealed class ResearchReadController(ComputeService compute) : ControllerBase
{
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private async Task<IActionResult> Run(string kind, object input, CancellationToken cancellation) => Ok(await compute.Run(UserId, kind, input, cancellation, accountSnapshot: true));
    private static void RunId(string value) { if (string.IsNullOrWhiteSpace(value) || value.Length > 128) throw new ArgumentException("决策运行ID无效"); }
    private static void Filter(string? symbol, int limit) { if (symbol is not null && FundFeeRepository.Symbol(symbol) != symbol || limit is < 1 or > 500) throw new ArgumentException("基金或条数无效"); }

    [HttpGet("data/status")]
    public Task<IActionResult> DataStatus(CancellationToken cancellation) => Run("analysis.data_status", new { }, cancellation);
    [HttpGet("decisions/latest")]
    public Task<IActionResult> Decision(CancellationToken cancellation) => Run("analysis.decision", new { }, cancellation);
    [HttpGet("backtests/latest")]
    public Task<IActionResult> Backtest(CancellationToken cancellation) => Run("analysis.backtest", new { }, cancellation);
    [HttpGet("stocks/candidates")]
    public Task<IActionResult> Stocks([FromQuery(Name = "run_id")] string runId, CancellationToken cancellation) { RunId(runId); return Run("analysis.stocks", new { run_id = runId }, cancellation); }
    [HttpGet("funds/candidates")]
    public Task<IActionResult> Funds([FromQuery(Name = "run_id")] string runId, CancellationToken cancellation) { RunId(runId); return Run("analysis.funds", new { run_id = runId }, cancellation); }
    [HttpGet("funds/lifecycle/events")]
    public Task<IActionResult> Lifecycle([FromQuery] string? symbol = null, [FromQuery] int limit = 100, CancellationToken cancellation = default) { Filter(symbol, limit); return Run("analysis.lifecycle", new { symbol, limit }, cancellation); }
    [HttpGet("funds/style-drift/events")]
    public Task<IActionResult> StyleDrift([FromQuery] string? symbol = null, [FromQuery] int limit = 100, CancellationToken cancellation = default) { Filter(symbol, limit); return Run("analysis.style_drift", new { symbol, limit }, cancellation); }
    [HttpGet("funds/valuations/alerts")]
    public Task<IActionResult> ValuationAlerts([FromQuery] string? symbol = null, [FromQuery] int limit = 100, CancellationToken cancellation = default) { Filter(symbol, limit); return Run("analysis.valuation_alerts", new { symbol, limit }, cancellation); }
    [HttpGet("ledger/attribution")]
    public Task<IActionResult> Profit(CancellationToken cancellation) => Run("analysis.profit", new { }, cancellation);
    [HttpGet("research/benchmarks/{benchmarkId}/compare")]
    public Task<IActionResult> Benchmark(string benchmarkId, [FromQuery(Name = "start_date")] DateOnly? startDate = null, [FromQuery(Name = "end_date")] DateOnly? endDate = null, CancellationToken cancellation = default)
    {
        RunId(benchmarkId); if (endDate < startDate) throw new ArgumentException("结束日期早于开始日期");
        return Run("analysis.benchmark", new { benchmark_id = benchmarkId, start_date = startDate, end_date = endDate }, cancellation);
    }
}
