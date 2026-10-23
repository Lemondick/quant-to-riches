using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Security;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class BranchIntegrationRepairTests
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

    [Fact]
    public void SmartDcaView_ClonesParsedJsonbRules()
    {
        var row = JsonNode.Parse("""{"id":"plan","base_amount":"100","rules_json":{"low_pct_boost":2,"high_pct_cut":0.5,"halt_pct":0.9}}""")!.AsObject();
        var view = SmartDcaRepository.View(row);
        Assert.Equal(2, view["rules"]!["low_pct_boost"]!.GetValue<int>());
        view["rules"]!["low_pct_boost"] = 3;
        Assert.Equal(2, row["rules_json"]!["low_pct_boost"]!.GetValue<int>());
    }

    [PgFact]
    public async Task SmartDcaCrud_ReturnsRulesAndKeepsAccountsIsolated()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        var repository = new SmartDcaRepository(new AccountDatabase(user.Connection));
        var rules = JsonNode.Parse("""{"low_pct_boost":2,"high_pct_cut":0.5,"halt_pct":0.9}""")!.AsObject();
        var created = await repository.CreateAsync(user.Id, "510300", 100m, rules, "monthly", new DateOnly(2026, 11, 1));
        var id = created["id"]!.GetValue<string>();
        try
        {
            Assert.Equal(2, created["rules"]!["low_pct_boost"]!.GetValue<int>());
            Assert.Equal(2, (await repository.GetAsync(user.Id, id))["rules"]!["low_pct_boost"]!.GetValue<int>());
            var listed = await repository.ListAsync(user.Id, null);
            Assert.Equal(2, listed[0]!["rules"]!["low_pct_boost"]!.GetValue<int>());
            Assert.Null(listed[0]!["rules_json"]);
            Assert.Empty(await repository.ListAsync(other.Id, null));
            rules["low_pct_boost"] = 3d;
            Assert.Equal(3, (await repository.UpdateAsync(user.Id, id, rules, null, "paused", null))["rules"]!["low_pct_boost"]!.GetValue<int>());
            await Assert.ThrowsAsync<QuantAssistant.Core.Errors.ApplicationFailure>(() => repository.GetAsync(other.Id, id));
            await repository.DeleteAsync(user.Id, id);
            Assert.Empty(await repository.ListAsync(user.Id, null));
        }
        finally { user.Execute("DELETE FROM smart_dca_plans WHERE user_id=$1", user.Id); }
    }

    [PgFact]
    public async Task DeterministicAsk_ReturnsOwnedDataWithoutCallingWorker()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var body = await Json(await client.PostAsJsonAsync("/api/v1/ask", new { question = "持仓盈亏排名" }));
        Assert.True(body.GetProperty("matched").GetBoolean());
        Assert.Equal("position_pnl_ranking", body.GetProperty("template").GetString());
        Assert.Equal(0, body.GetProperty("model_calls").GetInt32());
        Assert.Empty(body.GetProperty("data").GetProperty("ranking").EnumerateArray());
        Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task UnsupportedAsk_ReturnsSupportedScopeWithoutWorkerOrModelUsage()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var body = await Json(await client.PostAsJsonAsync("/api/v1/ask", new { question = "请写一首关于春天的诗" }));
        Assert.False(body.GetProperty("matched").GetBoolean());
        Assert.True(body.GetProperty("unsupported").GetBoolean());
        Assert.Equal(JsonValueKind.Null, body.GetProperty("template").ValueKind);
        Assert.Equal(0, body.GetProperty("model_calls").GetInt32());
        Assert.Contains("快捷问题", body.GetProperty("answer").GetString());
        Assert.Equal(10, body.GetProperty("supported_questions").GetArrayLength());
        Assert.Contains(body.GetProperty("supported_questions").EnumerateArray(), item => item.GetString() == "盈亏排名");
        Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task UnsupportedAsk_StillRequiresAuthentication()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        var response = await client.PostAsJsonAsync("/api/v1/ask", new { question = "请写一首关于春天的诗" });
        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [PgFact]
    public async Task AnonymousTwoFactorLogin_IssuesSessionAndConsumesChallengeOnce()
    {
        using var user = new PgTestUser();
        var auth = new AuthService(new AccountRepository(user.Connection), new Argon2PasswordHasher(), TimeProvider.System);
        var (secret, _) = auth.BeginTotpEnrollment(user.Id, "Synthetic");
        var codes = auth.ConfirmTotpEnrollment(user.Id, Totp.CurrentCode(secret, DateTimeOffset.UtcNow));
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        var first = await client.PostAsJsonAsync("/api/v1/auth/login", new { username = user.Username, password = PgTestUser.Password });
        var challenge = await Json(first);
        Assert.True(challenge.GetProperty("two_factor_required").GetBoolean());
        Assert.False(first.Headers.Contains("Set-Cookie"));
        var pending = challenge.GetProperty("pending_token").GetString();
        var verified = await client.PostAsJsonAsync("/api/v1/auth/2fa/verify", new { pending_token = pending, code = codes[0] });
        var session = await Json(verified);
        Assert.Equal(user.Id, session.GetProperty("user").GetProperty("id").GetString());
        Assert.NotEmpty(session.GetProperty("csrf_token").GetString()!);
        Assert.Contains(verified.Headers.GetValues("Set-Cookie"), value => value.StartsWith("quant_session=", StringComparison.Ordinal));
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/api/v1/auth/2fa/verify", new { pending_token = pending, code = codes[0] })).StatusCode);
        var second = auth.BeginTotpChallenge(user.Id);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/api/v1/auth/2fa/verify", new { pending_token = second, code = codes[0] })).StatusCode);
        var otp = await Json(await client.PostAsJsonAsync("/api/v1/auth/2fa/verify", new { pending_token = auth.BeginTotpChallenge(user.Id), code = Totp.CurrentCode(secret, DateTimeOffset.UtcNow) }));
        Assert.Equal(user.Id, otp.GetProperty("user").GetProperty("id").GetString());
    }

    [PgFact]
    public async Task TwoFactorVerification_RequiresOriginRejectsExpiredTokensAndLimitsAttempts()
    {
        using var user = new PgTestUser();
        var auth = new AuthService(new AccountRepository(user.Connection), new Argon2PasswordHasher(), TimeProvider.System);
        var (secret, _) = auth.BeginTotpEnrollment(user.Id, "Synthetic");
        auth.ConfirmTotpEnrollment(user.Id, Totp.CurrentCode(secret, DateTimeOffset.UtcNow));
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        var pending = auth.BeginTotpChallenge(user.Id);
        var payload = new { pending_token = pending, code = Totp.CurrentCode(secret, DateTimeOffset.UtcNow) };
        client.DefaultRequestHeaders.Remove("Origin");
        Assert.Equal(HttpStatusCode.Forbidden, (await client.PostAsJsonAsync("/api/v1/auth/2fa/verify", payload)).StatusCode);
        client.DefaultRequestHeaders.Add("Origin", "https://untrusted.invalid");
        Assert.Equal(HttpStatusCode.Forbidden, (await client.PostAsJsonAsync("/api/v1/auth/2fa/verify", payload)).StatusCode);
        client.DefaultRequestHeaders.Remove("Origin");
        client.DefaultRequestHeaders.Add("Origin", "http://localhost");
        user.Execute("UPDATE totp_login_challenges SET expires_at=now()-interval '1 second' WHERE user_id=$1", user.Id);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/api/v1/auth/2fa/verify", payload)).StatusCode);
        var limited = false;
        for (var index = 0; index < 21; index++)
        {
            var response = await client.PostAsJsonAsync("/api/v1/auth/2fa/verify", new { pending_token = "invalid", code = "000000" });
            limited |= response.StatusCode == HttpStatusCode.TooManyRequests;
        }
        Assert.True(limited, "Anonymous OTP guesses must share the bounded authentication rate limiter.");
    }
}
