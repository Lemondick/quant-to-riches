using System.Security.Cryptography;
using Microsoft.Extensions.Primitives;
using QuantAssistant.Api.Configuration;

namespace QuantAssistant.Api.Middleware;

/// <summary>
/// batch4 流 B（#8）：公开数据类 GET 接口的 ETag/Last-Modified 支持。
/// 仅对公开、非用户特定的 GET JSON 响应生效（行情/估值/日历/指数/宏观等），账本与写接口不参与。
/// 实现：缓冲响应体 → SHA-256 → 强 ETag；请求头 If-None-Match 命中时直接返回 304（无响应体），
/// 节省下行带宽。不在服务端缓存响应体：304 的判定完全来自请求头与本次响应体的哈希，
/// 因此不存在跨用户数据泄漏风险；同时响应体是本次请求实时生成的，数据新鲜度不受影响。
/// 开关：QUANT_ETAG_ENABLED（默认 true，见 QuantOptions.EtagEnabled）。
/// </summary>
public sealed class ETagMiddleware(RequestDelegate next, QuantOptions options)
{
    public async Task InvokeAsync(HttpContext context)
    {
        if (!options.EtagEnabled || !IsEligible(context))
        {
            await next(context);
            return;
        }

        var originalBody = context.Response.Body;
        await using var buffer = new MemoryStream();
        context.Response.Body = buffer;
        try
        {
            await next(context);
        }
        finally
        {
            context.Response.Body = originalBody;
        }

        var body = buffer.ToArray();
        if (context.Response.StatusCode != StatusCodes.Status200OK
            || !IsJson(context.Response.ContentType)
            || body.Length == 0)
        {
            await originalBody.WriteAsync(body, context.RequestAborted);
            return;
        }

        var etag = "\"" + Convert.ToHexString(SHA256.HashData(body)) + "\"";
        var now = DateTimeOffset.UtcNow;
        context.Response.Headers.ETag = etag;
        context.Response.Headers.LastModified = now.ToString("R");

        if (IfNoneMatchHits(context.Request.Headers.IfNoneMatch, etag))
        {
            // 304 必须无响应体：只保留校验头，Content-Length/Content-Type 随体一并去掉，
            // 避免客户端把上一次的 Content-Length 误用到空体上。
            context.Response.StatusCode = StatusCodes.Status304NotModified;
            context.Response.ContentLength = null;
            context.Response.ContentType = null;
            return;
        }

        await originalBody.WriteAsync(body, context.RequestAborted);
    }

    /// <summary>公开 GET JSON 路由白名单：行情/估值/日历/指数/宏观类；账本/持仓/写接口一律排除。</summary>
    internal static bool IsEligible(HttpContext context)
    {
        if (!HttpMethods.IsGet(context.Request.Method)) return false;
        var path = context.Request.Path.Value ?? "";
        return IsEligiblePath(path);
    }

    internal static bool IsEligiblePath(string path)
    {
        // 行情：snapshot/history/quotes/news；指数条目随 snapshot.index_registry / history(index_id) 一并返回。
        if (path.StartsWith("/api/v1/market/", StringComparison.OrdinalIgnoreCase)) return true;
        // 估值仪表盘。
        if (path.Equals("/api/v1/valuation", StringComparison.OrdinalIgnoreCase)) return true;
        // 基金估值列表（公开行情）。
        if (path.Equals("/api/v1/funds/valuations", StringComparison.OrdinalIgnoreCase)) return true;
        // 基金目录 / 分类（公开参考数据）。
        if (path.StartsWith("/api/v1/funds/catalog", StringComparison.OrdinalIgnoreCase)) return true;
        if (path.Equals("/api/v1/funds/categories", StringComparison.OrdinalIgnoreCase)) return true;
        // 单基金详情 /api/v1/funds/{symbol}/detail（注意：/api/v1/funds/health 等用户相关路由不在此列）。
        if (path.StartsWith("/api/v1/funds/", StringComparison.OrdinalIgnoreCase)
            && path.EndsWith("/detail", StringComparison.OrdinalIgnoreCase)) return true;
        // 交易日历（GET；AutoGenerate 等写接口是 POST/PUT，天然排除）。
        if (path.StartsWith("/api/v1/admin/calendar", StringComparison.OrdinalIgnoreCase)) return true;
        // 宏观观察 / 上下文（公开参考数据）。
        if (path.StartsWith("/api/v1/research/macro/", StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }

    private static bool IfNoneMatchHits(StringValues values, string etag)
    {
        foreach (var raw in values)
        {
            if (string.IsNullOrWhiteSpace(raw)) continue;
            foreach (var token in raw.Split(','))
            {
                var candidate = token.Trim();
                // 强比较：我们发出的是强 ETag，只接受完全一致或 *。
                if (candidate == "*" || string.Equals(candidate, etag, StringComparison.Ordinal)) return true;
            }
        }
        return false;
    }

    private static bool IsJson(string? contentType) =>
        contentType?.StartsWith("application/json", StringComparison.OrdinalIgnoreCase) == true;
}
