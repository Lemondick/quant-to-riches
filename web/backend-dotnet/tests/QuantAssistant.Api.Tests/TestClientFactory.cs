using Microsoft.AspNetCore.Mvc.Testing;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// 测试用 HttpClient 工厂。
/// E22 之后，所有 /api/v1 写请求都要求 Origin/Referer 与本站一致；
/// 测试客户端（TestServer 默认地址 http://localhost）统一带 Origin 头，
/// 避免 99 处调用点逐个补头。仅测试工程使用，不影响生产行为。
/// </summary>
internal static class TestClientFactory
{
    public static HttpClient CreateTestClient(this WebApplicationFactory<Program> factory)
    {
        var client = factory.CreateClient();
        client.DefaultRequestHeaders.Add("Origin", "http://localhost");
        return client;
    }
}
