using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class AutomaticAttributionTests
{
    private const string Route = "/api/v1/attribution/reports/automatic";
    private static object Body(string benchmark = "basket") => new { portfolio_symbol = "P1", benchmark_id = benchmark, start_date = "2026-01-02", end_date = "2026-01-30" };

    [PgFact]
    public async Task ForeignBenchmark_IsNotQueued_AndDoesNotCreateReport()
    {
        using var user = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        Assert.Equal(HttpStatusCode.NotFound, (await client.PostAsJsonAsync(Route, Body("another-account"))).StatusCode);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM attribution_reports WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task AutomaticReport_PersistsEvidence_DemotesManualEdits_AndRejectsStaleSources()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        var root = Path.Combine(Path.GetTempPath(), "quant-auto-api-" + Guid.NewGuid().ToString("N"));
        var directory = Directory.CreateDirectory(Path.Combine(root, "users", user.Id)).FullName;
        var file = Path.Combine(directory, "prices.parquet");
        File.WriteAllText(file, "synthetic-source");
        user.Execute("INSERT INTO benchmark_configs(user_id,benchmark_id,name,components_json,is_default,created_at,updated_at) VALUES($1,'basket','Synthetic','{\"600000.SH\":1}',false,now(),now())", user.Id);
        try
        {
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
            using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureAppConfiguration((_, config) => config.AddInMemoryCollection(new Dictionary<string, string?>
            { ["QUANT_SITE_ROOT"] = root, ["QUANT_WORKER_SECRET"] = "synthetic-automatic-attribution-worker-secret" })));
            using var client = factory.CreateTestClient();
            using var foreign = factory.CreateTestClient();
            PgTestUser.AttachSession(client, user.Connection, user.Id);
            PgTestUser.AttachSession(foreign, user.Connection, other.Id);
            var jobs = new ComputeJobRepository(user.Connection);
            async Task<HttpResponseMessage> Complete(JsonObject output, Action? change = null, bool ledger = false)
            {
                var pending = ledger ? client.PostAsJsonAsync(Route, new { portfolio_symbol="P1", benchmark_id="basket", start_date="2026-01-02",end_date="2026-01-30",ledger=true }) : client.PostAsJsonAsync(Route, Body());
                ComputeJob? claim = null;
                for (var i = 0; i < 100 && claim is null && !pending.IsCompleted; i++)
                { claim = jobs.Claim("synthetic-attribution", ["analysis.brinson"]); if (claim is null) await Task.Delay(50); }
                Assert.NotNull(claim);
                Assert.Equal(user.Id, claim.UserId);
                Assert.Equal("basket", JsonNode.Parse(claim.InputJson)!["benchmark_id"]!.GetValue<string>());
                change?.Invoke();
                Assert.True(jobs.Finish(claim.UserId, claim.JobId, claim.Attempt, claim.OwnerToken, output.ToJsonString(), null));
                return await pending;
            }
            JsonObject Ready() => new()
            {
                ["status"] = "ready", ["blockers"] = new JsonArray(),
                ["portfolio"] = JsonNode.Parse("[{\"symbol\":\"600000.SH\",\"sector_name\":\"科技\",\"initial_value\":\"200\",\"period_return\":\"0.1\"},{\"symbol\":\"600001.SH\",\"sector_name\":\"科技\",\"initial_value\":\"300\",\"period_return\":\"0.2\"},{\"symbol\":\"CASH.CNY\",\"sector_name\":\"现金\",\"initial_value\":\"500\",\"period_return\":\"0\"}]"),
                ["benchmark"] = JsonNode.Parse("[{\"symbol\":\"600000.SH\",\"sector_name\":\"科技\",\"initial_value\":\"0.4\",\"period_return\":\"0.1\"},{\"symbol\":\"600002.SH\",\"sector_name\":\"金融\",\"initial_value\":\"0.6\",\"period_return\":\"0.05\"}]"),
                ["provenance"] = new JsonObject { ["method"] = "static_holdings_price_return_v1", ["baseline_id"] = "synthetic-baseline", ["limitations"] = new JsonArray("固定持仓价格收益"), ["source_snapshots"] = new JsonArray() },
                ["private_files"] = new JsonArray(new JsonObject { ["path"] = file, ["sha256"] = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(file))).ToLowerInvariant() })
            };
            var created = await Complete(Ready());
            Assert.Equal(HttpStatusCode.Created, created.StatusCode);
            var saved = await created.Content.ReadFromJsonAsync<JsonElement>();
            var report = saved.GetProperty("view").GetProperty("report");
            Assert.Equal("native_static_price", report.GetProperty("data_source").GetString());
            Assert.Equal(0.01m, report.GetProperty("total_active_return").DecimalText());
            Assert.Equal(3, saved.GetProperty("view").GetProperty("details").GetArrayLength());
            Assert.DoesNotContain(directory, saved.ToString());
            var path = "/api/v1/attribution/reports/" + report.GetProperty("id").GetString();
            Assert.Equal(HttpStatusCode.NotFound, (await foreign.GetAsync(path)).StatusCode);
            var read = await client.GetFromJsonAsync<JsonElement>(path);
            Assert.True(JsonNode.DeepEquals(JsonNode.Parse(report.GetProperty("source_json").GetRawText()), JsonNode.Parse(read.GetProperty("report").GetProperty("source_json").GetRawText())));
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsJsonAsync(path + "/calculate", new { details = new[] { new { sector_name = "手工", portfolio_weight = 1m, benchmark_weight = 1m, portfolio_return = 0.1m, benchmark_return = 0.04m } } })).StatusCode);
            read = await client.GetFromJsonAsync<JsonElement>(path);
            Assert.Equal("manual_sector_input", read.GetProperty("report").GetProperty("data_source").GetString());
            Assert.Equal("{}", read.GetProperty("report").GetProperty("source_json").ToString());
            var blocked = await Complete(new() { ["status"] = "blocked", ["blockers"] = new JsonArray("缺少价格") });
            Assert.Equal(HttpStatusCode.OK, blocked.StatusCode);
            Assert.Equal(1L, user.Query("SELECT count(*) FROM attribution_reports WHERE user_id=$1", user.Id));
            var dynamicOutput = Ready();
            dynamicOutput["provenance"]!["method"] = "ledger_close_periods_v1";
            JsonObject Interval(string left, string right, string p, string b) => new()
            {
                ["start_date"] = left, ["end_date"] = right, ["portfolio_return"] = p,
                ["portfolio"] = JsonNode.Parse($"[{{\"symbol\":\"600000.SH\",\"sector_name\":\"科技\",\"initial_value\":\"1000\",\"period_return\":\"{p}\"}}]"),
                ["benchmark"] = JsonNode.Parse($"[{{\"symbol\":\"600000.SH\",\"sector_name\":\"科技\",\"initial_value\":\"1\",\"period_return\":\"{b}\"}}]")
            };
            dynamicOutput["periods"] = new JsonArray(Interval("2026-01-02","2026-01-15","0.1","0.04"), Interval("2026-01-15","2026-01-30","-0.05","0.02"));
            var linkedResponse = await Complete(dynamicOutput, ledger: true);
            Assert.Equal(HttpStatusCode.Created, linkedResponse.StatusCode);
            var linkedView = (await linkedResponse.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("view");
            var linkedReport = linkedView.GetProperty("report");
            Assert.Equal("native_linked_brinson", linkedReport.GetProperty("data_source").GetString());
            Assert.Equal(-.0158m, linkedReport.GetProperty("total_active_return").DecimalText());
            Assert.Equal(2, linkedReport.GetProperty("source_json").GetProperty("periods").GetArrayLength());
            Assert.Equal(linkedReport.GetProperty("id").GetString(), linkedReport.GetProperty("source_json").GetProperty("periods")[0].GetProperty("report").GetProperty("source_json").GetProperty("ledger_parent_report_id").GetString());
            Assert.Equal(4L, user.Query("SELECT count(*) FROM attribution_reports WHERE user_id=$1", user.Id));
            var periodId = linkedReport.GetProperty("source_json").GetProperty("periods")[0].GetProperty("report").GetProperty("id").GetString();
            Assert.Equal(HttpStatusCode.Conflict, (await client.PostAsJsonAsync("/api/v1/attribution/reports/"+periodId+"/calculate", new { details = new[] { new { sector_name="科技",portfolio_weight=1m,benchmark_weight=1m,portfolio_return=0m,benchmark_return=0m } } })).StatusCode);
            Assert.DoesNotContain(directory, linkedView.ToString());
            Assert.Equal(HttpStatusCode.Conflict, (await Complete(Ready(), () => File.AppendAllText(file, "changed"))).StatusCode);
            Assert.Equal(HttpStatusCode.Conflict, (await Complete(Ready(), () => user.Execute("UPDATE benchmark_configs SET name='Changed' WHERE user_id=$1", user.Id))).StatusCode);
            Assert.Equal(4L, user.Query("SELECT count(*) FROM attribution_reports WHERE user_id=$1", user.Id));
        }
        finally { user.Execute("DELETE FROM benchmark_configs WHERE user_id=$1", user.Id); Directory.Delete(root, true); }
    }
}
