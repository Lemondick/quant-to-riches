using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Caching.Memory;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch3 流 A：风险指标后端（压力测试见同文件的 Stress 端点，另独立 commit 记录）。
/// GET api/v1/risk/metrics → worker op risk.metrics（batch3 跨流契约），结果缓存 60 分钟。
/// 口径定义见 web/docs/功能说明.md §23.1。
/// </summary>
[ApiController]
[Route("api/v1/risk")]
public sealed class RiskController(ComputeService compute, IMemoryCache cache, QuantOptions options) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    /// <summary>
    /// 组合风险指标（VaR 历史模拟法 95%、最大回撤、年化波动率、夏普、卡玛）。
    /// portfolio_id 缺省用用户 id（其主组合）；period_days 默认 365，范围 30–1825。
    /// 结果按 QUANT_RISK_METRICS_CACHE_TTL_MIN（默认 60 分钟）缓存。
    /// </summary>
    [HttpGet("metrics")]
    public async Task<IActionResult> Metrics([FromQuery] string? portfolio_id = null, [FromQuery] int period_days = 365,
        CancellationToken cancellation = default)
    {
        var portfolioId = string.IsNullOrWhiteSpace(portfolio_id) ? Id : portfolio_id.Trim();
        if (portfolioId.Length is 0 or > 64) throw new ArgumentException("portfolio_id 无效。");
        if (period_days is < 30 or > 1825) throw new ArgumentException("period_days 须在 30 到 1825 之间。");
        var payload = new JsonObject { ["portfolio_id"] = portfolioId, ["period_days"] = period_days };
        var key = "risk:metrics:" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(payload.ToJsonString())));
        if (cache.TryGetValue(key, out JsonNode? hit) && hit is not null)
            return Ok(new JsonObject { ["cached"] = true, ["result"] = hit.DeepClone() });
        var result = (await compute.Run(Id, "risk.metrics", payload, cancellation, seconds: 180))?.AsObject()
            ?? throw new QuantAssistant.Core.Errors.ApplicationFailure("worker_unavailable", 503, "风险指标计算未完成，请稍后重试。");
        cache.Set(key, result.DeepClone(), new MemoryCacheEntryOptions
        {
            AbsoluteExpirationRelativeToNow = TimeSpan.FromMinutes(Math.Max(1, options.RiskMetricsCacheTtlMinutes)),
        });
        return Ok(new JsonObject { ["cached"] = false, ["result"] = result });
    }
}
