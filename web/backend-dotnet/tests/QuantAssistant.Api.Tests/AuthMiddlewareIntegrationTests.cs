using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using Xunit;

namespace QuantAssistant.Api.Tests;

public class AuthMiddlewareIntegrationTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly string _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION") ?? "";

    public AuthMiddlewareIntegrationTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        if (!string.IsNullOrEmpty(_connection))
        {
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", _connection);
        }
    }

    [PgFact]
    public async Task SessionMiddleware_PopulatesUserAndWorksWithLedger()
    {
        if (string.IsNullOrEmpty(_connection)) return;

        using var registration = new RegistrationScope();
        var client = _factory.CreateClient();

        // 1. Register
        var username = $"u{Guid.NewGuid():N}".Substring(0, 32);
        var regRes = await client.PostAsJsonAsync("/api/v1/auth/register", new { username, password = "IntegrationTest_12345" });
        if (!regRes.IsSuccessStatusCode) {
            throw new Exception("Reg failed: " + await regRes.Content.ReadAsStringAsync());
        }

        // 2. Login
        var loginRes = await client.PostAsJsonAsync("/api/v1/auth/login", new { username, password = "IntegrationTest_12345" });
        loginRes.EnsureSuccessStatusCode();
        var loginJson = await loginRes.Content.ReadFromJsonAsync<JsonElement>();
        var userId = loginJson.GetProperty("user").GetProperty("id").GetString();

        // 3. Extract cookie
        var cookies = loginRes.Headers.GetValues("Set-Cookie");
        var quantSession = cookies.FirstOrDefault(c => c.StartsWith("quant_session="));
        Assert.NotNull(quantSession);

        var cookieValue = quantSession.Split(';')[0].Substring("quant_session=".Length);

        // 4. Create new client with cookie, call a secured endpoint (e.g., /api/v1/history/onboarding which uses CurrentUserId())
        var authedClient = _factory.CreateClient();
        authedClient.DefaultRequestHeaders.Add("Cookie", $"quant_session={cookieValue}");

        var historyRes = await authedClient.GetAsync("/api/v1/onboarding");
        historyRes.EnsureSuccessStatusCode();

        var sessionRes = await authedClient.GetAsync("/api/v1/auth/session");
        sessionRes.EnsureSuccessStatusCode();
        var sessionJson = await sessionRes.Content.ReadFromJsonAsync<JsonElement>();
        var sessionUserId = sessionJson.GetProperty("user").GetProperty("id").GetString();

        Assert.Equal(userId, sessionUserId);

        // Cleanup
        using var conn = new NpgsqlConnection(_connection);
        conn.Open();
        using var cleanup = conn.CreateCommand();
        cleanup.CommandText = "DELETE FROM sessions WHERE user_id = $1";
        cleanup.Parameters.AddWithValue(userId!);
        cleanup.ExecuteNonQuery();
        cleanup.CommandText = "DELETE FROM account_activity WHERE user_id = $1";
        cleanup.ExecuteNonQuery();

        using var cleanup2 = conn.CreateCommand();
        cleanup2.CommandText = "DELETE FROM users WHERE id = $1";
        cleanup2.Parameters.AddWithValue(userId!);
        cleanup2.ExecuteNonQuery();
    }
}
