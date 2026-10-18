using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class AllOptimizationTests
{
    [PgFact]
    public async Task FilterAndExportAreOwned_PreserveDecimalsAndEscapeSpreadsheetFormula()
    {
        using var user = new PgTestUser(); using var other = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient(); using var foreign = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id); PgTestUser.AttachSession(foreign, user.Connection, other.Id);
        var created = await client.PostAsJsonAsync("/api/v1/attribution/reports", new { portfolio_symbol = "=SUM(1,2)", benchmark_symbol = "B", start_date = "2026-01-01", end_date = "2026-01-31", details = new[] { new { sector_name = "@行业\"", portfolio_weight = "1", benchmark_weight = "1", portfolio_return = "0.123456789123456789", benchmark_return = "0" } } });
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);
        var id = (await created.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetString();
        var route = "/api/v1/attribution/reports/" + id;
        var filtered = await client.GetFromJsonAsync<JsonElement>("/api/v1/attribution/reports?query=SUM&source=manual_sector_input&from=2026-01-01&to=2026-01-31");
        Assert.Equal(1, filtered.GetProperty("total").GetInt64());
        Assert.Equal(0, (await foreign.GetFromJsonAsync<JsonElement>("/api/v1/attribution/reports?query=SUM")).GetProperty("total").GetInt64());
        Assert.Equal(0, (await client.GetFromJsonAsync<JsonElement>("/api/v1/attribution/reports?from=2026-02-01")).GetProperty("total").GetInt64());
        Assert.Equal(HttpStatusCode.BadRequest, (await client.GetAsync("/api/v1/attribution/reports?from=2026-02-30")).StatusCode);
        Assert.Equal(HttpStatusCode.BadRequest, (await client.GetAsync("/api/v1/attribution/reports?source=invalid")).StatusCode);
        var export = await client.GetAsync(route + "/export?format=json");
        Assert.Equal("application/json", export.Content.Headers.ContentType!.MediaType);
        Assert.Equal("0.123456789123456789", (await export.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("report").GetProperty("total_active_return").GetString());
        var csv = await (await client.GetAsync(route + "/export?format=csv")).Content.ReadAsStringAsync();
        Assert.Contains("\"'=SUM(1,2)\"", csv); Assert.Contains("\"'@行业\"\"\"", csv);
        Assert.Contains("0.123456789123456789", csv);
        Assert.Equal(HttpStatusCode.NotFound, (await foreign.GetAsync(route + "/export?format=csv")).StatusCode);
        Assert.Equal(HttpStatusCode.BadRequest, (await client.GetAsync(route + "/export?format=xml")).StatusCode);
    }

    [PgFact]
    public void CostsBindRoleProviderStageAndPriceSnapshot_UnknownReasonsRemainExplicit()
    {
        using var user = new PgTestUser();
        var costs = new ModelCostRepository(new AccountDatabase(user.Connection));
        costs.Save(user.Id, new(0, Prices: new() { ["model"] = new(2m, 3m) }));
        user.Execute("INSERT INTO compute_jobs(user_id,job_id,kind,input_json,input_hash,status,owner_token,deadline,lease_expires_at) VALUES($1,'report','advisor.report','{}','x','running','owner',now()+interval '5 minutes',now()+interval '5 minutes')", user.Id);
        var id = costs.ReserveOwned(user.Id, "report", 1, "owner", "model", null, null, "news", "compatible_api");
        costs.FinishOwned(user.Id, "report", 1, "owner", id, 100, 50);
        costs.Save(user.Id, new(1, Prices: new() { ["model"] = new(100m, 100m) }));
        var known = costs.Read(user.Id)["items"]!.AsArray().Single()!;
        Assert.Equal("0.00035", known["cost"]!.GetValue<string>());
        Assert.Equal("news", known["role"]!.GetValue<string>());
        Assert.Equal("compatible_api", known["provider"]!.GetValue<string>());
        Assert.Null(known["unknown_reason"]);
        var timeout = costs.ReserveOwned(user.Id, "report", 1, "owner", "model", null, null, "reviewer", "compatible_api");
        costs.FinishOwned(user.Id, "report", 1, "owner", timeout, null, null, "read_timeout", "no_usage");
        var noPrice = costs.ReserveOwned(user.Id, "report", 1, "owner", "jev-model", null, null, "jev", "jev");
        costs.FinishOwned(user.Id, "report", 1, "owner", noPrice, 1, 2);
        var unknown = costs.Read(user.Id);
        Assert.Null(unknown["today"]!["total_cost"]);
        var items = unknown["items"]!.AsArray();
        Assert.Equal("read_timeout", items.Single(x => x!["id"]!.GetValue<string>() == timeout)!["stage"]!.GetValue<string>());
        Assert.Equal("missing_price", items.Single(x => x!["id"]!.GetValue<string>() == noPrice)!["unknown_reason"]!.GetValue<string>());
        Assert.Throws<ArgumentException>(() => costs.ReserveOwned(user.Id, "report", 1, "owner", "model", null, null, "sk-secret", "compatible_api"));
        Assert.Throws<ApplicationFailure>(() => costs.FinishOwned(user.Id, "report", 2, "wrong", timeout, 1, 2));
    }
}
