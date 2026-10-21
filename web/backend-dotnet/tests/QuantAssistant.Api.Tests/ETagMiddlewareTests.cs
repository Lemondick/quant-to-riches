using System.Text;
using Microsoft.AspNetCore.Http;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Middleware;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// batch4 流 B（#8）：ETagMiddleware 单元测试。纯 DefaultHttpContext，不依赖 PG/TestServer。
/// </summary>
public class ETagMiddlewareTests
{
    private static QuantOptions Options(bool enabled = true) =>
        new() { ConnectionString = "Host=localhost", EtagEnabled = enabled };

    private static DefaultHttpContext CreateContext(string method, string path)
    {
        var context = new DefaultHttpContext();
        context.Request.Method = method;
        context.Request.Path = path;
        context.Response.Body = new MemoryStream();
        return context;
    }

    private static RequestDelegate JsonWriter(string json, string contentType = "application/json; charset=utf-8") =>
        async ctx =>
        {
            ctx.Response.StatusCode = 200;
            ctx.Response.ContentType = contentType;
            await ctx.Response.WriteAsync(json, Encoding.UTF8);
        };

    private static string ReadBody(HttpContext context)
    {
        context.Response.Body.Seek(0, SeekOrigin.Begin);
        using var reader = new StreamReader(context.Response.Body, Encoding.UTF8, leaveOpen: true);
        return reader.ReadToEnd();
    }

    [Fact]
    public async Task Get_MarketSnapshot_SetsETag_And_LastModified()
    {
        var middleware = new ETagMiddleware(JsonWriter("{\"a\":1}"), Options());
        var context = CreateContext("GET", "/api/v1/market/snapshot");

        await middleware.InvokeAsync(context);

        Assert.Equal(200, context.Response.StatusCode);
        Assert.Equal("{\"a\":1}", ReadBody(context));
        var etag = context.Response.Headers.ETag.ToString();
        Assert.Matches("^\"[0-9A-F]{64}\"$", etag);
        Assert.False(string.IsNullOrEmpty(context.Response.Headers.LastModified.ToString()));
    }

    [Fact]
    public async Task Get_WithMatchingIfNoneMatch_Returns304_WithoutBody()
    {
        const string json = "{\"a\":1}";
        var first = new ETagMiddleware(JsonWriter(json), Options());
        var firstContext = CreateContext("GET", "/api/v1/valuation");
        await first.InvokeAsync(firstContext);
        var etag = firstContext.Response.Headers.ETag.ToString();
        Assert.NotEmpty(etag);

        var second = new ETagMiddleware(JsonWriter(json), Options());
        var context = CreateContext("GET", "/api/v1/valuation");
        context.Request.Headers.IfNoneMatch = etag;
        await second.InvokeAsync(context);

        Assert.Equal(304, context.Response.StatusCode);
        Assert.Equal("", ReadBody(context));
        Assert.Equal(etag, context.Response.Headers.ETag.ToString());
    }

    [Fact]
    public async Task Get_WithStaleIfNoneMatch_Returns200_WithNewETag()
    {
        var middleware = new ETagMiddleware(JsonWriter("{\"a\":1}"), Options());
        var context = CreateContext("GET", "/api/v1/market/quotes");
        context.Request.Headers.IfNoneMatch = "\"DEADBEEF\"";
        await middleware.InvokeAsync(context);

        Assert.Equal(200, context.Response.StatusCode);
        Assert.Equal("{\"a\":1}", ReadBody(context));
        Assert.NotEqual("\"DEADBEEF\"", context.Response.Headers.ETag.ToString());
    }

    [Fact]
    public async Task Get_WithWildcardIfNoneMatch_Returns304()
    {
        var middleware = new ETagMiddleware(JsonWriter("{\"a\":1}"), Options());
        var context = CreateContext("GET", "/api/v1/admin/calendar/status");
        context.Request.Headers.IfNoneMatch = "*";
        await middleware.InvokeAsync(context);

        Assert.Equal(304, context.Response.StatusCode);
        Assert.Equal("", ReadBody(context));
    }

    [Theory]
    [InlineData("/api/v1/market/history")]
    [InlineData("/api/v1/market/news")]
    [InlineData("/api/v1/valuation")]
    [InlineData("/api/v1/funds/valuations")]
    [InlineData("/api/v1/funds/catalog")]
    [InlineData("/api/v1/funds/categories")]
    [InlineData("/api/v1/funds/000001/detail")]
    [InlineData("/api/v1/admin/calendar")]
    [InlineData("/api/v1/research/macro/context")]
    public async Task EligiblePaths_GetETag(string path)
    {
        var middleware = new ETagMiddleware(JsonWriter("{}"), Options());
        var context = CreateContext("GET", path);
        await middleware.InvokeAsync(context);
        Assert.False(string.IsNullOrEmpty(context.Response.Headers.ETag.ToString()));
    }

    [Theory]
    [InlineData("/api/v1/ledger", "GET")]           // 账本：排除
    [InlineData("/api/v1/portfolio/history", "GET")] // 持仓：排除
    [InlineData("/api/v1/funds/health/latest", "GET")] // 用户体检：排除
    [InlineData("/api/v1/dashboard/summary", "GET")]  // 用户汇总：排除
    [InlineData("/api/v1/market/snapshot", "POST")]   // 写：排除
    [InlineData("/api/v1/admin/calendar", "POST")]    // 写：排除
    [InlineData("/api/v1/auth/login", "POST")]        // 登录：排除
    public async Task IneligibleRequests_PassThroughWithoutETag(string path, string method)
    {
        var middleware = new ETagMiddleware(JsonWriter("{}"), Options());
        var context = CreateContext(method, path);
        await middleware.InvokeAsync(context);
        Assert.Equal(200, context.Response.StatusCode);
        Assert.Equal("{}", ReadBody(context));
        Assert.True(string.IsNullOrEmpty(context.Response.Headers.ETag.ToString()));
    }

    [Fact]
    public async Task DisabledOption_PassesThroughWithoutETag()
    {
        var middleware = new ETagMiddleware(JsonWriter("{\"a\":1}"), Options(enabled: false));
        var context = CreateContext("GET", "/api/v1/market/snapshot");
        await middleware.InvokeAsync(context);
        Assert.Equal(200, context.Response.StatusCode);
        Assert.Equal("{\"a\":1}", ReadBody(context));
        Assert.True(string.IsNullOrEmpty(context.Response.Headers.ETag.ToString()));
    }

    [Fact]
    public async Task NonJsonResponse_NoETag()
    {
        var middleware = new ETagMiddleware(JsonWriter("<html/>", "text/html"), Options());
        var context = CreateContext("GET", "/api/v1/market/snapshot");
        await middleware.InvokeAsync(context);
        Assert.Equal(200, context.Response.StatusCode);
        Assert.True(string.IsNullOrEmpty(context.Response.Headers.ETag.ToString()));
    }
}
