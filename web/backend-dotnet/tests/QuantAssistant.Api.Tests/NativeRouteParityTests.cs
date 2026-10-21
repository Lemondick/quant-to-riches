using System.Reflection;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Routing;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Api.Controllers;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class NativeRouteParityTests
{
    [PgFact]
    public async Task PublishedWebRoot_ServesTheFrontendFromItsReleaseDirectory()
    {
        using var user = new PgTestUser();
        var previous = Environment.GetEnvironmentVariable("QUANT_WEB_ROOT");
        var directory = Path.Combine(Path.GetTempPath(), "quant-web-root-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        await File.WriteAllTextAsync(Path.Combine(directory, "index.html"), "NativeSyntheticShell");
        try
        {
            Environment.SetEnvironmentVariable("QUANT_WEB_ROOT", directory);
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
            using var factory = new WebApplicationFactory<Program>();
            using var client = factory.CreateTestClient();
            Assert.Equal("NativeSyntheticShell", await client.GetStringAsync("/"));
        }
        finally
        {
            Environment.SetEnvironmentVariable("QUANT_WEB_ROOT", previous);
            Directory.Delete(directory, recursive: true);
        }
    }

    // 原生重写中有意改名的路由：基线（旧 Python 站点 api-inventory.json）→ 现行原生路由。
    // 测试允许旧路由或其映射后继任者任一存在；新增改名必须在此登记，否则视为意外缺失。
    private static readonly Dictionary<string, string> RenamedRoutes = new(StringComparer.Ordinal)
    {
        ["GET /api/v1/admin/runtime"] = "GET /api/v1/runtime/status",
        ["GET /api/v1/alerts"] = "GET /api/v1/alerts/center",
        ["GET /api/v1/alerts/settings"] = "GET /api/v1/alerts/center/settings",
        ["PUT /api/v1/alerts/settings"] = "PUT /api/v1/alerts/center/settings",
        ["POST /api/v1/alerts/{}/acknowledge"] = "POST /api/v1/alerts/center/{}/acknowledge",
        ["GET /api/v1/health"] = "GET /healthz",
        ["GET /api/v1/ledger/actions"] = "GET /api/v1/ledger/corporate-actions",
        ["GET /api/v1/planning/cash-flows"] = "GET /api/v1/plans",
        ["GET /api/v1/planning/cash-flows/due"] = "GET /api/v1/plans/due",
        ["POST /api/v1/planning/cash-flows"] = "POST /api/v1/plans",
        ["GET /api/v1/portfolio/history"] = "GET /api/v1/ledger/portfolio-history",
        ["POST /api/v1/operations/watchdog/heartbeat"] = "POST /api/v1/operations/heartbeats",
    };

    [Fact]
    public void EveryBaselineRoute_HasANativeControllerAction()
    {
        var root = new DirectoryInfo(AppContext.BaseDirectory);
        while (root is not null && !Directory.Exists(Path.Combine(root.FullName, "migrations"))) root = root.Parent;
        Assert.NotNull(root);
        using var baseline = JsonDocument.Parse(File.ReadAllText(Path.Combine(root.Parent!.FullName, "docs", "refactor", "api-inventory.json")));
        static string Normalize(string path) => Regex.Replace("/" + path.Trim('/'), @"\{[^}]+\}", "{}");
        var routes = new HashSet<string>();
        foreach (var controller in typeof(AuthController).Assembly.GetTypes().Where(type => typeof(ControllerBase).IsAssignableFrom(type)))
        {
            var prefixes = controller.GetCustomAttributes<RouteAttribute>().Select(route => route.Template).DefaultIfEmpty("");
            foreach (var method in controller.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly))
                foreach (var attribute in method.GetCustomAttributes<HttpMethodAttribute>())
                    foreach (var prefix in prefixes)
                        foreach (var verb in attribute.HttpMethods)
                        {
                            var template = attribute.Template ?? "";
                            var path = template.StartsWith('/') || template.StartsWith("~/", StringComparison.Ordinal)
                                ? template.TrimStart('~') : prefix.TrimEnd('/') + "/" + template;
                            routes.Add(verb + " " + Normalize(path));
                        }
        }
        var missing = baseline.RootElement.GetProperty("routes").EnumerateArray()
            .Select(route => route.GetProperty("method").GetString() + " " + Normalize(route.GetProperty("path").GetString()!))
            .Where(route => !routes.Contains(route)
                && !(RenamedRoutes.TryGetValue(route, out var successor) && routes.Contains(successor)))
            .Distinct().Order().ToArray();
        Assert.True(missing.Length == 0, "Missing native routes: " + string.Join(", ", missing));
    }
}
