using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Research;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/research/funds")]
public sealed partial class FundResearchController(FundResearchRepository studies, ComputeService compute) : ControllerBase
{
    public sealed record RefreshBody(string[] Symbols);
    [GeneratedRegex("^[a-f0-9]{32}$")] private static partial Regex Identity();
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet("advisor-tracking")]
    public async Task<IActionResult> Tracking(int limit = 30, int page = 1, CancellationToken cancellation = default)
    { if (limit is < 1 or > 50 || page is < 1 or > 10000) throw new ArgumentException("分页参数无效"); return Ok(await compute.Run(Id, "fund.tracking", new { limit, page }, cancellation, accountSnapshot: true)); }

    [HttpGet("portfolio-risk")] public async Task<IActionResult> Risk(CancellationToken cancellation) => Ok(await compute.Run(Id, "fund.risk", new { }, cancellation, accountSnapshot: true));

    [HttpPost("public-observations")]
    public async Task<IActionResult> Refresh(RefreshBody body, CancellationToken cancellation)
    {
        if (body.Symbols.Length is < 1 or > 6) throw new ArgumentException("每次更新1至6只基金");
        var symbols = body.Symbols.Select(FundFeeRepository.Symbol).Distinct().ToArray();
        var result = (await compute.Run(Id, "fund.public_observations", new { symbols }, cancellation, accountSnapshot: true, seconds: 90))!.AsObject();
        if (result.Remove("_snapshots", out var rows) && rows is JsonArray snapshots)
            await studies.SaveSnapshotsAsync(Id, snapshots, cancellation);
        return Ok(result);
    }

    [HttpGet("cost-study")] public async Task<IActionResult> Study(CancellationToken cancellation) =>
        Ok(await compute.Run(Id, "fund.study", await studies.InputAsync(Id, cancellation: cancellation), cancellation, accountSnapshot: true));

    [HttpGet("cost-study/curves")]
    public async Task<IActionResult> Curves(string symbol, string period, [FromQuery(Name = "purchase_rate")] string rate, string version, CancellationToken cancellation)
    {
        if (FundFeeRepository.Symbol(symbol) != symbol || period is not ("full" or "early" or "late") || rate is not ("0" or "0.0012" or "0.012") || version.Length is < 1 or > 64) throw new ArgumentException("曲线参数无效");
        return Ok(await compute.Run(Id, "fund.curves", await studies.InputAsync(Id, new() { ["symbol"] = symbol, ["period"] = period, ["purchase_rate"] = rate, ["version"] = version }, cancellation), cancellation, accountSnapshot: true));
    }

    [HttpGet("cost-study/update")]
    public async Task<IActionResult> UpdateStatus(CancellationToken cancellation) => Ok(await studies.UpdateStatusAsync(Id, cancellation));

    [HttpPost("cost-study/update")]
    public async Task<IActionResult> Update(CancellationToken cancellation) => Accepted(await studies.EnqueueUpdateAsync(Id, cancellation));
}
