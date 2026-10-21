using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Api.Errors;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Infrastructure.Health;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// P1 契约测试：验证错误响应结构、健康端点、安全头与请求 ID 与旧站点契约一致。
/// </summary>
public sealed class ContractTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public ContractTests(WebApplicationFactory<Program> factory) => _factory = factory.WithWebHostBuilder(builder =>
        builder.ConfigureServices(services =>
        {
            services.RemoveAll<IReadinessProbe>();
            services.AddSingleton<IReadinessProbe, NotConfiguredProbe>();
        }));

    private HttpClient CreateClient() => _factory.CreateTestClient();

    [Fact]
    public async Task VueRoutes_ServeTheBuiltEntryOnDirectNavigation_WithoutMaskingMissingApiOrAssets()
    {
        var root = Path.Combine(Path.GetTempPath(), "quant-spa-contract-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        const string html = "<!doctype html><html><body>synthetic Vue entry</body></html>";
        await File.WriteAllTextAsync(Path.Combine(root, "index.html"), html);
        var previous = Environment.GetEnvironmentVariable("QUANT_WEB_ROOT");
        try
        {
            Environment.SetEnvironmentVariable("QUANT_WEB_ROOT", root);
            using var factory = new WebApplicationFactory<Program>();
            using var client = factory.CreateTestClient();
            foreach (var route in new[] { "/portfolio", "/ledger", "/watchlist", "/market", "/ai-stock", "/fund-review", "/quant", "/news", "/admin", "/settings" })
            {
                var response = await client.GetAsync(route);
                Assert.Equal(HttpStatusCode.OK, response.StatusCode);
                Assert.Equal("text/html", response.Content.Headers.ContentType!.MediaType);
                Assert.Equal(html, await response.Content.ReadAsStringAsync());
            }
            foreach (var missing in new[] { "/api/v1/does-not-exist", "/internal/does-not-exist", "/assets/missing.js", "/unknown-page" })
                Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync(missing)).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/v1/portfolio")).StatusCode);
        }
        finally
        {
            Environment.SetEnvironmentVariable("QUANT_WEB_ROOT", previous);
            Directory.Delete(root, recursive: true);
        }
    }

    private static async Task<JsonElement> ReadJson(HttpResponseMessage response)
    {
        var stream = await response.Content.ReadAsStreamAsync();
        using var doc = await JsonDocument.ParseAsync(stream);
        return doc.RootElement.Clone();
    }

    [Fact]
    public async Task UnknownRoute_Returns404_RouteNotFoundShape()
    {
        var response = await CreateClient().GetAsync("/api/v1/does-not-exist");
        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);

        var body = await ReadJson(response);
        Assert.Equal("route_not_found", body.GetProperty("error_code").GetString());
        Assert.True(body.GetProperty("message").GetString()?.Length > 0);
        Assert.False(string.IsNullOrWhiteSpace(body.GetProperty("request_id").GetString()));
    }

    [Fact]
    public async Task WrongMethod_Returns405_MethodNotAllowedShape()
    {
        var response = await CreateClient().PostAsync("/healthz", new StringContent("{}"));
        Assert.Equal(HttpStatusCode.MethodNotAllowed, response.StatusCode);

        var body = await ReadJson(response);
        Assert.Equal("method_not_allowed", body.GetProperty("error_code").GetString());
    }

    [Fact]
    public async Task Ready_Returns503_WhenProbeNotConfigured()
    {
        var response = await CreateClient().GetAsync("/readyz");
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);

        var body = await ReadJson(response);
        Assert.Equal("not_ready", body.GetProperty("status").GetString());
    }

    [Fact]
    public async Task Health_Returns503_Degraded_WhenProbeNotConfigured()
    {
        var response = await CreateClient().GetAsync("/healthz");
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);

        var body = await ReadJson(response);
        Assert.Equal("degraded", body.GetProperty("status").GetString());
    }

    [Fact]
    public async Task SecurityHeaders_ArePresent()
    {
        var response = await CreateClient().GetAsync("/readyz");
        Assert.Equal("nosniff", response.Headers.GetValues("X-Content-Type-Options").FirstOrDefault());
        Assert.Equal("DENY", response.Headers.GetValues("X-Frame-Options").FirstOrDefault());
        Assert.Equal("no-referrer", response.Headers.GetValues("Referrer-Policy").FirstOrDefault());
        Assert.Equal("no-store", response.Headers.GetValues("Cache-Control").FirstOrDefault());
    }

    [Fact]
    public async Task RequestId_IsEchoed()
    {
        var client = CreateClient();
        var request = new HttpRequestMessage(HttpMethod.Get, "/readyz");
        request.Headers.Add("X-Request-ID", "test-request-123");

        var response = await client.SendAsync(request);
        Assert.Equal("test-request-123", response.Headers.GetValues("X-Request-ID").FirstOrDefault());
    }

    [Fact]
    public void ExceptionTranslator_MapsBusinessErrors()
    {
        Assert.Equal((422, ErrorCodes.FeeEvidenceUnavailable),
            ExceptionTranslator.Translate(new UnverifiedFeeScheduleError()));
        Assert.Equal((409, ErrorCodes.AdvisorReportActive),
            ExceptionTranslator.Translate(new AdvisorBusyError()));
        Assert.Equal((503, ErrorCodes.DatabaseUnavailable),
            ExceptionTranslator.Translate(new DatabaseUnavailableError()));
        Assert.Equal((500, ErrorCodes.InternalError),
            ExceptionTranslator.Translate(new InvalidOperationException("boom")));
    }
}
