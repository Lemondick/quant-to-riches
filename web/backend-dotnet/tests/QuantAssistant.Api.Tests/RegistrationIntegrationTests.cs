using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Infrastructure.Auth;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class RegistrationIntegrationTests
{
    [PgFact]
    public async Task RegistrationExplainsPauseAndSupportsNormalizedUsersAndLogin()
    {
        using var admin = new PgTestUser();
        var repository = new AccountRepository(admin.Connection);
        var previouslyEnabled = repository.RegistrationEnabled();
        var created = new List<string>();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", admin.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateTestClient();
        var username = "reg_" + Guid.NewGuid().ToString("N")[..12];
        const string password = "RegistrationTest_12345";
        try
        {
            admin.Execute("UPDATE site_settings SET value='0' WHERE key='registration_enabled'");
            if (repository.CountAdmins() == 0)
                await Error(await client.PostAsJsonAsync("/api/v1/auth/register", new { username, password }),
                    HttpStatusCode.Conflict, "registration_closed");
            admin.Execute("UPDATE users SET role='admin' WHERE id=$1", admin.Id);
            await Error(await client.PostAsJsonAsync("/api/v1/auth/register", new { username, password }),
                HttpStatusCode.Conflict, "registration_paused");
            admin.Execute("UPDATE site_settings SET value='1' WHERE key='registration_enabled'");
            await Error(await client.PostAsJsonAsync("/api/v1/auth/register", new { username, password = "short" }),
                HttpStatusCode.UnprocessableEntity, "invalid_password");
            var registered = await client.PostAsJsonAsync("/api/v1/auth/register", new { username = " " + username.ToUpperInvariant() + " ", password });
            Assert.Equal(HttpStatusCode.Created, registered.StatusCode);
            var user = (await registered.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("user");
            created.Add(user.GetProperty("id").GetString()!);
            Assert.Equal(username, user.GetProperty("username").GetString());
            Assert.Equal("user", user.GetProperty("role").GetString());
            Assert.False(user.TryGetProperty("password_hash", out _));
            await Error(await client.PostAsJsonAsync("/api/v1/auth/register", new { username, password }),
                HttpStatusCode.Conflict, "username_taken");
            var login = await client.PostAsJsonAsync("/api/v1/auth/login", new { username, password });
            Assert.Equal(HttpStatusCode.OK, login.StatusCode);
            var cookie = login.Headers.GetValues("Set-Cookie").Single(value => value.StartsWith("quant_session=")).Split(';')[0];
            using var userClient = factory.CreateClient(new WebApplicationFactoryClientOptions { HandleCookies = false });
            userClient.DefaultRequestHeaders.Add("Cookie", cookie);
            Assert.Equal(HttpStatusCode.OK, (await userClient.GetAsync("/api/v1/auth/session")).StatusCode);
            Assert.Equal(HttpStatusCode.Forbidden, (await userClient.GetAsync("/api/v1/admin/users")).StatusCode);
            using var foreignOrigin = new HttpRequestMessage(HttpMethod.Post, "/api/v1/auth/register")
                { Content = JsonContent.Create(new { username = username + "x", password }) };
            foreignOrigin.Headers.Add("Origin", "https://foreign.invalid");
            await Error(await client.SendAsync(foreignOrigin), HttpStatusCode.Forbidden, "origin_forbidden");
        }
        finally
        {
            Cleanup(admin, created);
            admin.Execute("UPDATE site_settings SET value=$1 WHERE key='registration_enabled'", previouslyEnabled ? "1" : "0");
        }
    }

    [PgFact]
    public async Task LastRegistrationSlotIsAtomicAndExistingUsersCanStillLogin()
    {
        using var registration = new RegistrationScope();
        using var helper = new PgTestUser();
        var repository = new AccountRepository(helper.Connection);
        var created = new List<string>();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", helper.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateTestClient();
        try
        {
            while (repository.CountUsers() < 29)
            {
                var id = Guid.NewGuid().ToString("N");
                helper.Execute("INSERT INTO users(id,username,password_hash,role,status,created_at) VALUES ($1,$2,$3,'user','active',now())",
                    id, "capacity_" + id[..12], "synthetic-unused-hash");
                created.Add(id);
            }
            Assert.Equal(29, repository.CountUsers());
            var responses = await Task.WhenAll(Enumerable.Range(0, 2).Select(_ =>
                client.PostAsJsonAsync("/api/v1/auth/register", new { username = "race_" + Guid.NewGuid().ToString("N")[..12], password = PgTestUser.Password })));
            foreach (var response in responses)
                if (response.StatusCode == HttpStatusCode.Created)
                    created.Add((await response.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("user").GetProperty("id").GetString()!);
            Assert.Single(responses, response => response.StatusCode == HttpStatusCode.Created);
            await Error(responses.Single(response => response.StatusCode == HttpStatusCode.Conflict), HttpStatusCode.Conflict, "registration_full");
            var status = await client.GetFromJsonAsync<JsonElement>("/api/v1/auth/status");
            Assert.Equal(30, status.GetProperty("registered").GetInt32());
            Assert.Equal(0, status.GetProperty("remaining").GetInt32());
            Assert.False(status.GetProperty("registration_open").GetBoolean());
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsJsonAsync("/api/v1/auth/login", new { username = helper.Username, password = PgTestUser.Password })).StatusCode);
        }
        finally { Cleanup(helper, created); }
    }

    private static async Task Error(HttpResponseMessage response, HttpStatusCode status, string code)
    {
        Assert.Equal(status, response.StatusCode);
        Assert.Equal(code, (await response.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("error_code").GetString());
    }

    private static void Cleanup(PgTestUser helper, IEnumerable<string> users)
    {
        foreach (var id in users)
        {
            helper.Execute("DELETE FROM sessions WHERE user_id=$1", id);
            helper.Execute("DELETE FROM account_activity WHERE user_id=$1", id);
            helper.Execute("DELETE FROM users WHERE id=$1", id);
        }
    }
}
