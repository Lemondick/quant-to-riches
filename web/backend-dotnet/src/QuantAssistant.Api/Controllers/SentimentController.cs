using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Caching.Memory;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch5 任务3：市场情绪指标后端。
/// GET api/v1/sentiment → worker op sentiment.fetch（契约），
/// 结果按 QUANT_SENTIMENT_CACHE_MIN（默认 60 分钟）缓存。
/// 无合规源时 worker 返回空 items + warning，不虚构数据。
/// </summary>
[ApiController]
[Route("api/v1/sentiment")]
public sealed class SentimentController(ComputeService compute, IMemoryCache cache, QuantOptions options) : ControllerBase
{
    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpGet]
    public async Task<IActionResult> Get(CancellationToken cancellation = default)
    {
        const string key = "sentiment:CN";
        if (cache.TryGetValue(key, out JsonNode? hit) && hit is not null)
            return Ok(new JsonObject { ["cached"] = true, ["result"] = hit.DeepClone() });
        var payload = new JsonObject { ["markets"] = new JsonArray("CN") };
        var result = (await compute.Run(Id, "sentiment.fetch", payload, cancellation, seconds: 120))?.AsObject()
            ?? throw new QuantAssistant.Core.Errors.ApplicationFailure("worker_unavailable", 503, "情绪数据抓取未完成，请稍后重试。");
        cache.Set(key, result.DeepClone(), new MemoryCacheEntryOptions
        {
            AbsoluteExpirationRelativeToNow = TimeSpan.FromMinutes(Math.Max(1, options.SentimentCacheMinutes)),
        });
        return Ok(new JsonObject { ["cached"] = false, ["result"] = result });
    }
}
