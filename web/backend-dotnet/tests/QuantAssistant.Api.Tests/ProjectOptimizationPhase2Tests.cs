using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Infrastructure.Db;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class ProjectOptimizationPhase2Tests
{
    private static WebApplicationFactory<Program> Factory(string connection)
    {
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", connection);
        return new WebApplicationFactory<Program>();
    }

    private static async Task<JsonElement> Json(HttpResponseMessage response)
    {
        Assert.True(response.IsSuccessStatusCode, $"{response.StatusCode}: {await response.Content.ReadAsStringAsync()}");
        return await response.Content.ReadFromJsonAsync<JsonElement>();
    }

    [PgFact]
    public async Task NetWorth_ExplicitlyMarksUnrecordedLiabilitiesAndPositionsScope()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var summary = await Json(await client.GetAsync("/api/v1/net-worth"));
        Assert.False(summary.GetProperty("liabilities_recorded").GetBoolean());
        Assert.Equal("positions_only", summary.GetProperty("calculation_scope").GetString());
        Assert.Equal(summary.GetProperty("total_assets").GetString(), summary.GetProperty("net_worth").GetString());
        Assert.Contains("负债", summary.GetProperty("liabilities_note").GetString());
    }
}
