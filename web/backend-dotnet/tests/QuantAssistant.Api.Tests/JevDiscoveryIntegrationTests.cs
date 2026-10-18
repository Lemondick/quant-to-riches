using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using QuantAssistant.Api.Services;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class JevDiscoveryIntegrationTests
{
    private sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> send) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => Task.FromResult(send(request));
    }
    private sealed class AsyncHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => send(request, token);
    }

    [PgFact]
    public async Task PendingDiscovery_DoesNotHoldDatabaseLock_OrOverwriteAReplacementConnection()
    {
        using var user = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        var root = Path.Combine(Path.GetTempPath(), "quant-jev-race-" + Guid.NewGuid().ToString("N"));
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var keys = new List<string>();
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse("8.8.8.8") }), _ => new AsyncHandler(async (request, token) =>
        {
            keys.Add(request.Headers.Authorization!.Parameter!);
            entered.TrySetResult();
            await release.Task.WaitAsync(token);
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{\"models\":[{\"name\":\"jev-latest\"}]}") };
        }));
        var secrets = new PrivateSecretStore(new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_SECRET_ROOT"] = root }).Build(), new EphemeralDataProtectionProvider());
        try
        {
            using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureTestServices(services =>
            {
                services.RemoveAll<PublicModelCatalog>(); services.AddSingleton(catalog);
                services.RemoveAll<PrivateSecretStore>(); services.AddSingleton(secrets);
            }));
            using var client = factory.CreateClient();
            PgTestUser.AttachSession(client, user.Connection, user.Id);
            Assert.Equal(HttpStatusCode.OK, (await client.PutAsJsonAsync("/api/v1/advisor/jev-connection", new { api_key = "synthetic-old-key", revision = 0 })).StatusCode);
            var pending = client.PostAsJsonAsync("/api/v1/advisor/jev-models/discover", new { api_key = "", revision = 1 });
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(4));
            var replacement = await client.PutAsJsonAsync("/api/v1/advisor/jev-connection", new { api_key = "synthetic-replacement-key", revision = 1 }).WaitAsync(TimeSpan.FromSeconds(4));
            Assert.Equal(HttpStatusCode.OK, replacement.StatusCode);
            release.TrySetResult();
            Assert.Equal(HttpStatusCode.Conflict, (await pending).StatusCode);
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsJsonAsync("/api/v1/advisor/jev-models/discover", new { api_key = "", revision = 2 })).StatusCode);
            Assert.Equal(new[] { "synthetic-old-key", "synthetic-replacement-key" }, keys);
        }
        finally
        {
            release.TrySetResult();
            user.Execute("DELETE FROM advisor_model_settings WHERE user_id=$1", user.Id);
            user.Execute("DELETE FROM user_documents WHERE user_id=$1", user.Id);
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }

    [PgFact]
    public async Task Discovery_ReusesEncryptedKey_RejectsStaleAndBadKeys_AndIsAccountScoped()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        var root = Path.Combine(Path.GetTempPath(), "quant-jev-discovery-" + Guid.NewGuid().ToString("N"));
        var keys = new List<string>();
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse("8.8.8.8") }), _ => new Handler(request =>
        {
            Assert.Equal("https://api.typesafe.ai/v1/models", request.RequestUri!.AbsoluteUri);
            keys.Add(request.Headers.Authorization!.Parameter!);
            return new HttpResponseMessage(keys[^1] == "synthetic-bad-key" ? HttpStatusCode.Unauthorized : HttpStatusCode.OK)
            { Content = new StringContent("{\"models\":[{\"name\":\"jev-latest\"},{\"name\":\"jev-preview\"}]}") };
        }));
        var secrets = new PrivateSecretStore(new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_SECRET_ROOT"] = root }).Build(), new EphemeralDataProtectionProvider());
        try
        {
            using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureTestServices(services =>
            {
                services.RemoveAll<PublicModelCatalog>();
                services.AddSingleton(catalog);
                services.RemoveAll<PrivateSecretStore>();
                services.AddSingleton(secrets);
            }));
            using var client = factory.CreateClient();
            using var otherClient = factory.CreateClient();
            PgTestUser.AttachSession(client, user.Connection, user.Id);
            PgTestUser.AttachSession(otherClient, user.Connection, other.Id);
            var response = await client.PostAsJsonAsync("/api/v1/advisor/jev-models/discover", new { api_key = "synthetic-good-key", revision = 0 });
            Assert.Equal(HttpStatusCode.OK, response.StatusCode);
            var settings = await response.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal(1, settings.GetProperty("jev").GetProperty("revision").GetInt32());
            Assert.Equal(2, settings.GetProperty("jev").GetProperty("models").GetArrayLength());
            Assert.True(settings.GetProperty("jev").GetProperty("has_key").GetBoolean());
            Assert.DoesNotContain("synthetic-good-key", settings.ToString());
            Assert.DoesNotContain("key_reference", settings.ToString());
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsJsonAsync("/api/v1/advisor/jev-models/discover", new { api_key = "", revision = 1 })).StatusCode);
            Assert.Equal(new[] { "synthetic-good-key", "synthetic-good-key" }, keys);
            Assert.Equal(HttpStatusCode.Conflict, (await client.PostAsJsonAsync("/api/v1/advisor/jev-models/discover", new { api_key = "synthetic-new-key", revision = 1 })).StatusCode);
            Assert.Equal(2, keys.Count);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync("/api/v1/advisor/jev-models/discover", new { api_key = "synthetic-bad-key", revision = 2 })).StatusCode);
            var unchanged = await client.GetFromJsonAsync<JsonElement>("/api/v1/advisor/model-settings");
            Assert.Equal(2, unchanged.GetProperty("jev").GetProperty("revision").GetInt32());
            Assert.Single(Directory.GetFiles(root, "*.secret"));
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsJsonAsync("/api/v1/advisor/jev-models/discover", new { api_key = "", revision = 2 })).StatusCode);
            Assert.Equal("synthetic-good-key", keys[^1]);
            var isolated = await otherClient.GetFromJsonAsync<JsonElement>("/api/v1/advisor/model-settings");
            Assert.False(isolated.GetProperty("jev").GetProperty("has_key").GetBoolean());
            Assert.Empty(isolated.GetProperty("jev").GetProperty("models").EnumerateArray());
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await otherClient.PostAsJsonAsync("/api/v1/advisor/jev-models/discover", new { api_key = "", revision = 0 })).StatusCode);
        }
        finally
        {
            user.Execute("DELETE FROM advisor_model_settings WHERE user_id=$1", user.Id);
            user.Execute("DELETE FROM user_documents WHERE user_id=$1", user.Id);
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }
}
