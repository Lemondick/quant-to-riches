using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Research;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/research")]
public sealed class ResearchMetadataController(ResearchMetadataRepository metadata) : ControllerBase
{
    public sealed record FactorBody(string Factor, DateOnly AsOfDate, double? Ic = null, double? Ir = null, int SampleCount = 0, string Source = "manual");
    public sealed record MacroBody(string Indicator, DateOnly ObservedOn, double Value, string Unit, string Source = "manual");
    public sealed record BenchmarkBody(string Name, Dictionary<string, double> Components, string? BenchmarkId = null, bool IsDefault = false);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static void Text(string value, int maximum)
    { if (string.IsNullOrWhiteSpace(value) || value.Length > maximum) throw new ArgumentException("文本参数无效"); }
    private static void Limit(int limit, int minimum = 1, int maximum = 500)
    { if (limit < minimum || limit > maximum) throw new ArgumentException("条数无效"); }

    [HttpPost("factors/observations")]
    public async Task<IActionResult> RecordFactor([FromBody] FactorBody body, CancellationToken cancellation = default)
    {
        Text(body.Factor, 80); Text(body.Source, 200);
        if (body.SampleCount < 0 || body.Ic is { } ic && (!double.IsFinite(ic) || ic is < -1 or > 1)
            || body.Ir is { } ir && !double.IsFinite(ir)) throw new ArgumentException("因子观测无效");
        return Ok(await metadata.RecordFactorAsync(UserId, body.Factor.Trim(), body.AsOfDate, body.Ic, body.Ir, body.SampleCount, body.Source.Trim(), cancellation));
    }

    [HttpGet("factors/alerts")]
    public async Task<IActionResult> FactorAlerts([FromQuery] string? factor = null, [FromQuery] int limit = 100, CancellationToken cancellation = default)
    {
        Limit(limit); if (factor is not null) Text(factor, 80);
        return Ok(new { items = await metadata.ListFactorAlertsAsync(UserId, factor, limit, cancellation) });
    }

    [HttpGet("factors/{factor}")]
    public async Task<IActionResult> FactorMonitor(string factor, [FromQuery] int limit = 60, CancellationToken cancellation = default)
    {
        Text(factor, 80); Limit(limit, 2, 730);
        return Ok(await metadata.FactorReportAsync(UserId, factor.Trim(), limit, cancellation));
    }

    [HttpGet("macro/observations")]
    public async Task<IActionResult> MacroObservations([FromQuery] string? indicator = null, [FromQuery] int limit = 100, CancellationToken cancellation = default)
    {
        Limit(limit); if (indicator is not null) Text(indicator, 60);
        return Ok(new { items = await metadata.ListMacroObservationsAsync(UserId, indicator, limit, cancellation) });
    }

    [HttpPost("macro/observations")]
    public async Task<IActionResult> RecordMacro([FromBody] MacroBody body, CancellationToken cancellation = default)
    {
        if (!ResearchMetadataRepository.Indicators.Contains(body.Indicator) || !double.IsFinite(body.Value)) throw new ArgumentException("宏观观测无效");
        Text(body.Unit, 30); Text(body.Source, 200);
        return Ok(await metadata.RecordMacroAsync(UserId, body.Indicator, body.ObservedOn, body.Value, body.Unit.Trim(), body.Source.Trim(), cancellation));
    }

    [HttpGet("macro/context")]
    public async Task<IActionResult> MacroContext([FromQuery(Name = "as_of")] DateOnly? asOf = null, CancellationToken cancellation = default)
    {
        var target = asOf ?? DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        return Ok(await metadata.MacroContextAsync(UserId, target, cancellation));
    }

    [HttpGet("benchmarks")]
    public async Task<IActionResult> Benchmarks([FromQuery] int limit = 100, CancellationToken cancellation = default)
    {
        Limit(limit);
        return Ok(new { items = await metadata.ListBenchmarksAsync(UserId, limit, cancellation) });
    }

    [HttpPost("benchmarks")]
    public async Task<IActionResult> CreateBenchmark([FromBody] BenchmarkBody body, CancellationToken cancellation = default)
    {
        Text(body.Name, 200); if (body.BenchmarkId is not null) Text(body.BenchmarkId, 128);
        if (body.Name.Any(char.IsControl) || body.BenchmarkId?.Any(char.IsControl) == true) throw new ArgumentException("基准名称或标识无效");
        if (body.Components is null || body.Components.Count is < 1 or > 20 || body.Components.Any(c => string.IsNullOrWhiteSpace(c.Key) || c.Key.Trim().Length > 80 || !double.IsFinite(c.Value) || c.Value <= 0)
            || Math.Abs(body.Components.Values.Sum() - 1) > 0.00000001) throw new ArgumentException("基准权重须为正且合计为1");
        var components = new Dictionary<string, double>(StringComparer.Ordinal);
        foreach (var (symbol, weight) in body.Components)
        {
            var code = symbol.Trim().ToUpperInvariant();
            if (code == "CASH.CNY") code = "CASH";
            if (code.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)) || !components.TryAdd(code, weight))
                throw new ArgumentException("基准代码不能含空白或重复，现金代码使用CASH");
        }
        var id = body.BenchmarkId?.Trim() ?? Guid.NewGuid().ToString("N");
        return Ok(await metadata.CreateBenchmarkAsync(UserId, id, body.Name.Trim(), components, body.IsDefault, cancellation));
    }
}
