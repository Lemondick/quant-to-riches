using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Api.Controllers;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Market;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class IndexApiTests
{
    private static readonly DateTimeOffset Now = DateTimeOffset.Parse("2026-10-20T10:01:00+08:00");
    private static JsonObject Snapshot(params JsonObject[] rows) => new()
    {
        ["indices"] = new JsonArray(rows.Cast<JsonNode>().ToArray()), ["fund_rankings"] = new JsonArray(),
        ["sector_rankings"] = new JsonArray(), ["observed_at"] = Now.ToString("O"),
        ["unavailable"] = new JsonArray(), ["stale"] = false,
    };
    private static JsonObject Quote() => JsonNode.Parse("""{"symbol":"000001","name":"上证指数","asset_type":"index","price":"100","change":"0","change_rate":"0","source":"tencent","source_time":"2026-10-20T10:00:00+08:00","fetched_at":"2026-10-20T10:00:01+08:00","trade_time_verified":true}""")!.AsObject();
    private static JsonObject Read(JsonObject data)
    {
        var snapshot = new PublicMarketSnapshot(new ManualClock(Now));
        Assert.True(snapshot.Publish(data, Now.UtcDateTime));
        return snapshot.Read(null).Data!.AsObject();
    }
    [Fact]
    public void LegacySnapshotGetsDetachedRegistryAndSixteenIndependentStatuses()
    {
        var data = Read(Snapshot(Quote()));
        Assert.Equal(IndexRegistry.Version, data["registry_version"]!.GetValue<string>());
        Assert.Equal(16, data["index_registry"]!.AsArray().Count);
        Assert.Equal(16, data["index_statuses"]!.AsArray().Count);
        Assert.Equal("cn.sse.composite", data["indices"]![0]!["id"]!.GetValue<string>());
        Assert.Equal("CNY", data["indices"]![0]!["currency"]!.GetValue<string>());
        Assert.Equal("valid", data["index_statuses"]![0]!["quality_status"]!.GetValue<string>());
        Assert.Equal("unavailable", data["index_statuses"]![11]!["quality_status"]!.GetValue<string>());
        data["index_registry"]![0]!["name"] = "mutated";
        Assert.Equal("上证指数", IndexRegistry.Get("cn.sse.composite").Name);
        Assert.DoesNotContain("user_id", data["index_registry"]!.ToJsonString());
    }
    [Theory]
    [InlineData("id", "us.sp500")]
    [InlineData("market", "US")]
    [InlineData("name", "平安银行")]
    [InlineData("asset_type", "stock")]
    public void ConflictingLegacyIdentityIsNeverRelabelledAsAChineseIndex(string key, string value)
    {
        var row = Quote(); row[key] = value;
        var data = Read(Snapshot(row));
        Assert.Empty(data["indices"]!.AsArray());
        Assert.Equal("invalid", data["index_statuses"]![0]!["quality_status"]!.GetValue<string>());
    }
    [Fact]
    public void ForeignQuoteKeepsOwnTimezoneAndMissingSourceClockStaysMissing()
    {
        var row = JsonNode.Parse("""{"id":"us.sp500","symbol":".INX","name":"标普500","asset_type":"index","market":"US","price":"100","change_rate":"0","source":"tencent","source_time":"2026-10-19T16:00:00-04:00","fetched_at":"2026-10-20T10:00:01+08:00"}""")!.AsObject();
        var missing = Quote(); missing["source_time"] = null; missing["observed_at"] = null;
        var data = Read(Snapshot(row, missing));
        var actual = Assert.Single(data["indices"]!.AsArray().OfType<JsonObject>(), x => x["id"]!.GetValue<string>() == "us.sp500");
        Assert.Equal("US", actual["market"]!.GetValue<string>());
        Assert.Equal("America/New_York", actual["market_timezone"]!.GetValue<string>());
        Assert.Equal("valid", actual["quality_status"]!.GetValue<string>());
        Assert.Equal("2026-10-19T16:00:00-04:00", actual["source_time"]!.GetValue<string>());
        Assert.Null(data["indices"]!.AsArray().OfType<JsonObject>().Single(x => x["id"]!.GetValue<string>() == "cn.sse.composite")["source_time"]);
    }
    [Fact]
    public void CacheFailureCannotBePromotedToFreshOnNativeRead()
    {
        var row = Quote(); row["cache_fallback"] = true; row["is_stale"] = true;
        var data = Read(Snapshot(row));
        Assert.True(data["indices"]![0]!["is_stale"]!.GetValue<bool>());
        Assert.Equal("stale", data["indices"]![0]!["quality_status"]!.GetValue<string>());
        Assert.Equal("index_refresh_failed", data["index_statuses"]![0]!["fallback_reason"]!.GetValue<string>());
        Assert.Equal("2026-10-20T10:00:01+08:00", data["indices"]![0]!["fetched_at"]!.GetValue<string>());
    }
    [Theory]
    [InlineData("invalid")]
    [InlineData("unavailable")]
    [InlineData("unrecognized")]
    public void SupplierFailureIsNotPromotedByClockReclassification(string quality)
    {
        var row=Quote();row["quality_status"]=quality;row["quality_reason"]="supplier_rejected_observation";
        var data=Read(Snapshot(row));
        Assert.Empty(data["indices"]!.AsArray());
        Assert.Equal(quality=="unavailable"?quality:"invalid",data["index_statuses"]![0]!["quality_status"]!.GetValue<string>());
    }
    private static MarketController Controller(ComputeService? compute = null, string id = "synthetic")
    {
        var controller = new MarketController(compute!, null!, null!, null!, null!);
        controller.ControllerContext = new ControllerContext { HttpContext = new DefaultHttpContext { User = new ClaimsPrincipal(new ClaimsIdentity([new Claim("sub", id)], "synthetic")) } };
        return controller;
    }
    [Theory]
    [InlineData(null, "unknown")]
    [InlineData("600000", null)]
    [InlineData("000001", "cn.csi.300")]
    [InlineData(null, null)]
    public async Task InvalidOrConflictingHistoryIdentifiersFailBeforeAnyJob(string? symbol, string? id)
        => await Assert.ThrowsAsync<ArgumentException>(() => Controller().History(symbol, CancellationToken.None, indexId: id));
    [Fact]
    public async Task UnknownIndexQuoteCodeFailsBeforeDatabaseLookup()
        => await Assert.ThrowsAsync<ArgumentException>(() => Controller().Quotes(null, ["600000"], CancellationToken.None));
    [Fact]
    public async Task PreferredHistoryIdCanReadTheLegacyMemoryEntryWithoutDatabase()
    {
        const string connection = "Host=127.0.0.1;Port=1;Database=offline;Username=offline;Timeout=1";
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes("{\"symbol\":\"000300\"}")));
        memory.Set("compute:synthetic:market.history:" + digest, JsonNode.Parse("""{"symbol":"000300","items":[{"date":"2026-10-19","close":"100"}],"stale":false}"""));
        var compute = new ComputeService(new ComputeJobRepository(connection), new AnalysisSnapshotRepository(new AccountDatabase(connection)), new ConfigurationBuilder().Build(), memory);
        var result = Assert.IsType<OkObjectResult>(await Controller(compute).History(null, CancellationToken.None, indexId: "cn.csi.300"));
        var data = Assert.IsType<JsonObject>(result.Value);
        Assert.Equal("cn.csi.300", data["id"]!.GetValue<string>());
        Assert.Equal("cn.csi.300", data["index_id"]!.GetValue<string>());
        Assert.Equal("100", data["items"]![0]!["close"]!.GetValue<string>());
    }
    [PgFact]
    public async Task AllFourLegacyHistoryCacheInputsAreSharedByPreferredIdAndOldSymbol()
    {
        using var user = new PgTestUser();
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var jobs = new ComputeJobRepository(user.Connection);
        var compute = new ComputeService(jobs, new AnalysisSnapshotRepository(new AccountDatabase(user.Connection)), new ConfigurationBuilder().Build(), memory);
        var controller = Controller(compute, user.Id);
        foreach (var definition in IndexRegistry.All.Where(x => x.LegacyHistorySymbol is not null))
        {
            var input = new JsonObject { ["symbol"] = definition.LegacyHistorySymbol }.ToJsonString();
            var job = jobs.Enqueue(user.Id, "market.history", input, DateTime.UtcNow.AddMinutes(2));
            var claim = jobs.Claim("index-cache-test", ["market.history"])!;
            Assert.Equal(job, claim.JobId);
            var result = new JsonObject { ["symbol"] = definition.LegacyHistorySymbol, ["items"] = new JsonArray(new JsonObject { ["date"] = "2026-10-19", ["close"] = "100" }), ["stale"] = false };
            Assert.True(jobs.Finish(user.Id, job, claim.Attempt, claim.OwnerToken, result.ToJsonString(), null));
            foreach (var preferred in new[] { false, true })
            {
                var response = Assert.IsType<OkObjectResult>(await controller.History(preferred ? null : definition.LegacyHistorySymbol, CancellationToken.None, indexId: preferred ? definition.Id : null));
                var data = Assert.IsType<JsonObject>(response.Value);
                Assert.Equal(definition.Id, data["id"]!.GetValue<string>());
                Assert.Equal(definition.LegacyHistorySymbol, data["symbol"]!.GetValue<string>());
                Assert.Equal("100", data["items"]![0]!["close"]!.GetValue<string>());
            }
        }
        Assert.Equal(4L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }
}
