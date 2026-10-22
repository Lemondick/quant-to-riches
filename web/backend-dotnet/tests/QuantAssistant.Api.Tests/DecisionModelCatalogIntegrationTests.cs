using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class DecisionModelCatalogIntegrationTests
{
    [PgFact]
    public async Task SaveModels_RejectsDecisionModelMissingFromJevCatalog_AndAcceptsListedModel()
    {
        using var user = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        var root = Path.Combine(Path.GetTempPath(), "quant-decision-models-" + Guid.NewGuid().ToString("N"));
        try
        {
            using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder =>
                builder.ConfigureAppConfiguration((_, config) => config.AddInMemoryCollection(
                    new Dictionary<string, string?> { ["QUANT_SECRET_ROOT"] = root })));
            using var client = factory.CreateClient();
            PgTestUser.AttachSession(client, user.Connection, user.Id);
            var secrets = factory.Services.GetRequiredService<PrivateSecretStore>();
            var providerRef = secrets.Save(user.Id, "synthetic-provider-key");
            var jevRef = secrets.Save(user.Id, "synthetic-jev-key");

            user.Execute(
                "INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,'model-provider',1,$2::jsonb)",
                user.Id,
                JsonSerializer.Serialize(new
                {
                    revision = 1,
                    endpoint = "https://models.example.org/v1",
                    models = new[] { "news-model", "strategy-model", "reviewer-model" },
                    key_reference = providerRef
                }));
            user.Execute(
                "INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,'model-jev',1,$2::jsonb)",
                user.Id,
                JsonSerializer.Serialize(new
                {
                    revision = 1,
                    models = new[] { "jev-latest" },
                    key_reference = jevRef
                }));
            user.Execute("INSERT INTO advisor_provider_selection(user_id,id,provider_revision) VALUES($1,1,1)", user.Id);

            var invalid = await client.PutAsJsonAsync("/api/v1/advisor/model-settings", new
            {
                revision = 0,
                models = new
                {
                    news = "news-model",
                    strategy = "strategy-model",
                    reviewer = "reviewer-model",
                    decision = "not-in-jev-catalog"
                }
            });
            Assert.Equal(HttpStatusCode.UnprocessableEntity, invalid.StatusCode);
            Assert.Equal(0L, user.Query("SELECT count(*) FROM advisor_model_settings WHERE user_id=$1", user.Id));

            var valid = await client.PutAsJsonAsync("/api/v1/advisor/model-settings", new
            {
                revision = 0,
                models = new
                {
                    news = "news-model",
                    strategy = "strategy-model",
                    reviewer = "reviewer-model",
                    decision = "jev-latest"
                }
            });
            Assert.Equal(HttpStatusCode.OK, valid.StatusCode);
            var view = await valid.Content.ReadFromJsonAsync<JsonElement>();
            Assert.True(view.GetProperty("configured").GetBoolean());
            Assert.True(view.GetProperty("decision_model_valid").GetBoolean());
            Assert.Equal("jev-latest", view.GetProperty("models").GetProperty("decision").GetString());
        }
        finally
        {
            user.Execute("DELETE FROM advisor_provider_selection WHERE user_id=$1", user.Id);
            user.Execute("DELETE FROM advisor_model_settings WHERE user_id=$1", user.Id);
            user.Execute("DELETE FROM user_documents WHERE user_id=$1", user.Id);
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }
}
