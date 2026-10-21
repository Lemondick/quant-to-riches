using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Caching.Memory;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Market;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务2：估值仪表盘后端。
/// GET api/v1/valuation → worker op valuation.percentiles（契约§2），
/// 结果按 QUANT_VALUATION_CACHE_TTL_MIN（默认360分钟）缓存。
/// indices 参数：逗号分隔的 symbol 或 symbol:market；缺省取 IndexRegistry 中已验证的 CN 指数（最多12个）。
/// </summary>
[ApiController]
[Route("api/v1/valuation")]
public sealed class ValuationController(ComputeService compute, IMemoryCache cache, QuantOptions options) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet]
    public async Task<IActionResult> Get([FromQuery] string? indices = null, [FromQuery] int years = 10,
        CancellationToken cancellation = default)
    {
        if (years is < 3 or > 30) throw new ArgumentException("years 须在 3 到 30 之间。");
        var list = ParseIndices(indices);
        var payload = new JsonObject
        {
            ["indices"] = new JsonArray(list.Select(i => (JsonNode)new JsonObject { ["symbol"] = i.Symbol, ["market"] = i.Market }).ToArray()),
            ["years"] = years,
        };
        var key = "valuation:" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(payload.ToJsonString())));
        if (cache.TryGetValue(key, out JsonNode? hit) && hit is not null)
            return Ok(new JsonObject { ["cached"] = true, ["result"] = hit.DeepClone() });
        var result = (await compute.Run(Id, "valuation.percentiles", payload, cancellation, seconds: 180))?.AsObject()
            ?? throw new QuantAssistant.Core.Errors.ApplicationFailure("worker_unavailable", 503, "估值计算未完成，请稍后重试。");
        cache.Set(key, result.DeepClone(), new MemoryCacheEntryOptions
        {
            AbsoluteExpirationRelativeToNow = TimeSpan.FromMinutes(Math.Max(1, options.ValuationCacheTtlMinutes)),
        });
        return Ok(new JsonObject { ["cached"] = false, ["result"] = result });
    }

    private static List<(string Symbol, string Market)> ParseIndices(string? raw)
    {
        if (!string.IsNullOrWhiteSpace(raw))
        {
            var parsed = new List<(string, string)>();
            foreach (var part in raw.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            {
                var segments = part.Split(':', 2);
                var symbol = segments[0].Trim();
                if (symbol.Length is 0 or > 32) throw new ArgumentException("指数代码无效。");
                parsed.Add((symbol, segments.Length > 1 ? segments[1].Trim().ToUpperInvariant() : "CN"));
            }
            if (parsed.Count is 0 or > 20) throw new ArgumentException("指数数量须在 1 到 20 之间。");
            return parsed;
        }
        // 默认：已验证行情的 CN 指数（与 PublicSnapshotRefresher 的 quoteIndices 同口径）。
        return IndexRegistry.All
            .Where(row => row.Enabled && row.Market == "CN" && row.Quote.VerificationStatus == "verified")
            .OrderBy(row => row.DisplayOrder)
            .Take(12)
            .Select(row => (row.ExchangeCode, "CN"))
            .ToList();
    }
}
