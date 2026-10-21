using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class FundExecutionReplayTests
{
    [PgFact]
    public async Task FrozenReplay_IsOwned_Exportable_AndRejectsTampering()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) => config.AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = "synthetic-fund-replay-worker-secret-123" })));
        using var client = factory.CreateTestClient();
        using var foreign = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        PgTestUser.AttachSession(foreign, user.Connection, other.Id);
        const string route = "/api/v1/research/funds/execution-replays";
        var pending = client.PostAsJsonAsync(route, new { bundle = new { version = "fund-execution-evidence-v1", symbol = "000001" } });
        var jobs = new ComputeJobRepository(user.Connection);
        ComputeJob? claim = null;
        for (var i = 0; i < 100 && claim is null && !pending.IsCompleted; i++) { claim = jobs.Claim("replay-fixture", ["fund.execution_replay"]); if (claim is null) await Task.Delay(30); }
        Assert.NotNull(claim);
        Assert.DoesNotContain("snapshot", claim.InputJson);
        Assert.Equal(user.Id, claim.UserId);
        const string result = "{\"status\":\"blocked\",\"blockers\":[\"missing evidence\"],\"net_return\":null,\"fills\":[],\"curve\":[],\"rejections\":[],\"research_only\":true,\"qualifies_strategy\":false,\"qualifies_paper_days\":false,\"ready_for_trading\":false}";
        Assert.True(jobs.Finish(user.Id, claim.JobId, claim.Attempt, claim.OwnerToken, result, null));
        var response = await pending;
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var saved = (await response.Content.ReadFromJsonAsync<JsonObject>())!;
        var id = saved["id"]!.GetValue<string>();
        Assert.Equal(64, saved["fingerprint"]!.GetValue<string>().Length);
        var path = route + "/" + id;
        Assert.True(JsonNode.DeepEquals(saved, await client.GetFromJsonAsync<JsonObject>(path)));
        Assert.Equal(HttpStatusCode.NotFound, (await foreign.GetAsync(path)).StatusCode);
        Assert.Empty((await foreign.GetFromJsonAsync<JsonObject>(route))!["items"]!.AsArray());
        var export = await client.GetAsync(path + "/export");
        Assert.Equal(HttpStatusCode.OK, export.StatusCode);
        Assert.Contains(id, export.Content.Headers.ContentDisposition!.FileName);
        user.Execute("UPDATE user_documents SET payload=jsonb_set(payload,'{result,status}','\"completed\"') WHERE user_id=$1 AND document_key=$2", user.Id, "fund-execution-replay:" + id);
        Assert.Equal(HttpStatusCode.Conflict, (await client.GetAsync(path)).StatusCode);
        var damaged = (await client.GetFromJsonAsync<JsonObject>(route))!["items"]!.AsArray().Single()!;
        Assert.Equal(id, damaged["id"]!.GetValue<string>());
        Assert.False(damaged["integrity_valid"]!.GetValue<bool>());
        Assert.Null(damaged["symbol"]);
        Assert.Equal(HttpStatusCode.NotFound, (await foreign.DeleteAsync(path)).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await client.DeleteAsync(path)).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync(path)).StatusCode);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM transactions WHERE user_id=$1", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM model_call_costs WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task MalformedArchive_DoesNotHideOtherArchives_AndCanBeRemoved()
    {
        using var user = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        const string route = "/api/v1/research/funds/execution-replays";
        foreach (var raw in new[] { "[]", "null", "{\"fingerprint\":123}", "{\"id\":false}" })
        {
            var id = Guid.NewGuid().ToString("N");
            user.Execute("INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,$2,1,$3::jsonb)", user.Id, "fund-execution-replay:" + id, raw);
            Assert.Equal(HttpStatusCode.Conflict, (await client.GetAsync(route + "/" + id)).StatusCode);
            Assert.Equal(HttpStatusCode.Conflict, (await client.GetAsync(route + "/" + id + "/export")).StatusCode);
        }
        var items = (await client.GetFromJsonAsync<JsonObject>(route))!["items"]!.AsArray();
        Assert.Equal(4, items.Count);
        foreach (var item in items)
        {
            Assert.False(item!["integrity_valid"]!.GetValue<bool>());
            Assert.Null(item["symbol"]);
            Assert.Equal("corrupted", item["status"]!.GetValue<string>());
            Assert.Equal(HttpStatusCode.OK, (await client.DeleteAsync(route + "/" + item["id"]!.GetValue<string>())).StatusCode);
        }
        Assert.Empty((await client.GetFromJsonAsync<JsonObject>(route))!["items"]!.AsArray());
    }

    [PgFact]
    public async Task ConcurrentRequestRetries_KeepOneArchive_AndRejectChangedInput()
    {
        using var user = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) => config.AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_WORKER_SECRET"] = "synthetic-fund-replay-worker-secret-123" })));
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        const string route = "/api/v1/research/funds/execution-replays";
        var id = Guid.NewGuid().ToString("N");
        var body = new { request_id = id, bundle = new { version = "fund-execution-evidence-v1", symbol = "000001", capital = "1000.00" } };
        var first = client.PostAsJsonAsync(route, body);
        var second = client.PostAsJsonAsync(route, body);
        var jobs = new ComputeJobRepository(user.Connection);
        ComputeJob? claim = null;
        for (var i = 0; i < 100 && claim is null; i++) { claim = jobs.Claim("replay-retry-fixture", ["fund.execution_replay"]); if (claim is null) await Task.Delay(30); }
        Assert.NotNull(claim);
        const string result = "{\"status\":\"blocked\",\"blockers\":[\"missing evidence\"],\"net_return\":null,\"fills\":[],\"curve\":[],\"rejections\":[]}";
        Assert.True(jobs.Finish(user.Id, claim.JobId, claim.Attempt, claim.OwnerToken, result, null));
        var responses = await Task.WhenAll(first, second);
        Assert.Contains(responses, r => r.StatusCode == HttpStatusCode.Created);
        Assert.Contains(responses, r => r.StatusCode == HttpStatusCode.OK);
        var saved = (await responses[0].Content.ReadFromJsonAsync<JsonObject>())!;
        Assert.Equal(id, saved["id"]!.GetValue<string>());
        Assert.True(JsonNode.DeepEquals(saved, await responses[1].Content.ReadFromJsonAsync<JsonObject>()));
        Assert.Equal(1L, user.Query("SELECT count(*) FROM audit_log WHERE user_id=$1 AND entity_type='fund_execution_replay'", user.Id));
        Assert.Equal(HttpStatusCode.Conflict, (await client.PostAsJsonAsync(route, new { request_id = id, bundle = new { symbol = "000001", capital = "999" } })).StatusCode);
        for (var i = 0; i < 19; i++) user.Execute("INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,$2,1,'{}'::jsonb)", user.Id, "fund-execution-replay:" + Guid.NewGuid().ToString("N"));
        var retry = await client.PostAsJsonAsync(route, body);
        Assert.Equal(HttpStatusCode.OK, retry.StatusCode);
        Assert.True(JsonNode.DeepEquals(saved, await retry.Content.ReadFromJsonAsync<JsonObject>()));
        Assert.Equal(1L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync(route, new { request_id = "bad", bundle = new { symbol = "000001" } })).StatusCode);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync(route, new { bundle = new { symbol = 1 } })).StatusCode);
    }
}
