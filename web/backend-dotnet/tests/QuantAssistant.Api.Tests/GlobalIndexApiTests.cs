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
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Market;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class GlobalIndexApiTests
{
    public static IEnumerable<object[]> NewIds() => IndexRegistry.All.Where(x => x.LegacyHistorySymbol is null).Select(x => new object[] { x.Id });
    private static JsonObject Data(IndexDefinition row) => new()
    {
        ["id"] = row.Id, ["symbol"] = row.ExchangeCode, ["market"] = row.Market, ["currency"] = row.Currency,
        ["source"] = row.History!.Provider, ["source_symbol"] = row.History.Symbol, ["stale"] = false,
        ["items"] = new JsonArray(new JsonObject { ["date"] = "2026-10-16", ["open"] = "100", ["high"] = "110", ["low"] = "90", ["close"] = "105", ["is_partial"] = false }),
    };
    private static MarketController Controller(ComputeService service, string user = "synthetic")
    {
        var controller = new MarketController(service, null!, null!, null!, null!);
        controller.ControllerContext = new() { HttpContext = new DefaultHttpContext { User = new ClaimsPrincipal(new ClaimsIdentity([new Claim("sub", user)], "synthetic")) } };
        return controller;
    }
    private static MarketController Offline(IMemoryCache memory, IndexDefinition definition, JsonObject data)
    {
        const string connection = "Host=127.0.0.1;Port=1;Database=offline;Username=offline;Timeout=1";
        var input = new JsonObject { ["index_id"] = definition.Id }.ToJsonString();
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(input)));
        memory.Set("compute:synthetic:market.history:" + hash, data.DeepClone());
        return Controller(new ComputeService(new ComputeJobRepository(connection), new AnalysisSnapshotRepository(new AccountDatabase(connection)), new ConfigurationBuilder().Build(), memory));
    }
    [Theory, MemberData(nameof(NewIds))]
    public async Task AllNewIdsReadTheirOwnCacheWithoutDatabase(string id)
    {
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var row = IndexRegistry.Get(id);
        var controller = Offline(memory, row, Data(row));
        var result = Assert.IsType<OkObjectResult>(await controller.History(null, CancellationToken.None, indexId: id));
        var data = Assert.IsType<JsonObject>(result.Value);
        Assert.Equal(id, data["id"]!.GetValue<string>());
        Assert.Equal("105", Assert.Single(data["items"]!.AsArray())!["close"]!.GetValue<string>());
        Assert.Equal(row.Market, data["market"]!.GetValue<string>());
        data["items"]!.AsArray().Clear();
        var again = Assert.IsType<OkObjectResult>(await controller.History(null, CancellationToken.None, indexId: id));
        Assert.Single(Assert.IsType<JsonObject>(again.Value)["items"]!.AsArray());
    }
    [Theory]
    [InlineData("id", "us.sp500")]
    [InlineData("symbol", ".INX")]
    [InlineData("market", "US")]
    [InlineData("currency", "USD")]
    [InlineData("source_symbol", "us.INX")]
    [InlineData("source", "unverified")]
    public async Task ConflictingCachedIdentityIsRejectedInsteadOfRelabelled(string field, string value)
    {
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var row = IndexRegistry.Get("hk.hsi");
        var data = Data(row); data[field] = value;
        var error = await Assert.ThrowsAsync<ApplicationFailure>(() => Offline(memory, row, data).History(null, CancellationToken.None, indexId: row.Id));
        Assert.Equal(503, error.Status);
    }
    [Fact]
    public async Task NewHistoryWithoutResponseIdIsRejected()
    {
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var row = IndexRegistry.Get("hk.hsi"); var data = Data(row); data.Remove("id");
        await Assert.ThrowsAsync<ApplicationFailure>(() => Offline(memory, row, data).History(null, CancellationToken.None, indexId: row.Id));
    }
    [Fact]
    public async Task InvalidOrEmptyHistoryIsDiscardedFromSuccessfulMemoryCache()
    {
        foreach(var failure in new[]{"identity","invalid","unrecognized","empty"})
        {
            using var memory=new MemoryCache(new MemoryCacheOptions());
            var row=IndexRegistry.Get("hk.hsi");var data=Data(row);
            if(failure=="identity")data["currency"]="USD";
            else if(failure=="empty")data["items"]=new JsonArray();
            else data["quality_status"]=failure;
            var error=await Assert.ThrowsAsync<ApplicationFailure>(()=>Offline(memory,row,data).History(null,CancellationToken.None,indexId:row.Id));
            Assert.Equal("public_snapshot_unavailable",error.Code);
            Assert.Equal(0,memory.Count);
        }
    }
    [PgFact]
    public async Task NewHistoryIdsHaveSeparatePersistedInputsAndNoDuplicateJobsOnRead()
    {
        using var user = new PgTestUser();
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var jobs = new ComputeJobRepository(user.Connection);
        var controller = Controller(new ComputeService(jobs, new AnalysisSnapshotRepository(new AccountDatabase(user.Connection)), new ConfigurationBuilder().Build(), memory), user.Id);
        foreach (var row in IndexRegistry.All.Where(x => x.LegacyHistorySymbol is null))
        {
            var input = new JsonObject { ["index_id"] = row.Id }.ToJsonString();
            var job = jobs.Enqueue(user.Id, "market.history", input, DateTime.UtcNow.AddMinutes(2));
            var claim = jobs.Claim("global-index-cache", ["market.history"])!;
            Assert.Equal(job, claim.JobId);
            Assert.True(jobs.Finish(user.Id, job, claim.Attempt, claim.OwnerToken, Data(row).ToJsonString(), null));
            var result = Assert.IsType<OkObjectResult>(await controller.History(null, CancellationToken.None, indexId: row.Id));
            Assert.Equal(row.Id, Assert.IsType<JsonObject>(result.Value)["id"]!.GetValue<string>());
            if (row.Market == "CN")
            {
                var legacy = Assert.IsType<OkObjectResult>(await controller.History(row.ExchangeCode, CancellationToken.None));
                Assert.Equal(row.Id, Assert.IsType<JsonObject>(legacy.Value)["id"]!.GetValue<string>());
            }
        }
        Assert.Equal(12L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }
    [Fact]
    public void HistoryReadCannotPromoteProviderInvalidStatusOrOldDatesToValid()
    {
        var now = DateTimeOffset.Parse("2026-10-20T15:00:00Z");
        var invalid = Data(IndexRegistry.Get("us.sp500")); invalid["quality_status"] = "invalid"; invalid["quality_reason"] = "identity_mismatch";
        PublicDataQuality.Apply("market.history", invalid, now);
        Assert.Equal("invalid", invalid["quality_status"]!.GetValue<string>());
        invalid["quality_status"]="unrecognized";
        PublicDataQuality.Apply("market.history", invalid, now);
        Assert.Equal("invalid",invalid["quality_status"]!.GetValue<string>());
        var stale = Data(IndexRegistry.Get("us.sp500"));
        PublicDataQuality.Apply("market.history", stale, now);
        Assert.Equal("stale", stale["quality_status"]!.GetValue<string>());
        Assert.Equal("2026-10-16", stale["updated_at"]!.GetValue<string>());
        Assert.Null(stale["source_time"]);
    }
    [Fact]
    public void RestingSnapshotSurvivesAWeekendWithoutChangingItsSourceTime()
    {
        var clock = new ManualClock(DateTimeOffset.Parse("2026-10-16T08:00:00Z"));
        var store = new PublicMarketSnapshot(clock);
        var data = JsonNode.Parse("""{"indices":[{"symbol":"000300","name":"沪深300","asset_type":"index","price":"100","change":"0","change_rate":"0","source":"synthetic","source_time":"2026-10-16T15:00:00+08:00"}],"fund_rankings":[],"sector_rankings":[],"observed_at":"2026-10-16T15:00:00+08:00","stale":false,"unavailable":[]}""")!.AsObject();
        Assert.True(store.Publish(data, clock.GetUtcNow().UtcDateTime));
        clock.Advance(TimeSpan.FromDays(2));
        var result = store.Read(null);
        Assert.Equal("stale", result.Cache);
        Assert.Equal("2026-10-16T15:00:00+08:00", result.Data!["indices"]![0]!["source_time"]!.GetValue<string>());
        Assert.Equal("2026-10-16T15:00:00+08:00", result.Data["observed_at"]!.GetValue<string>());
        clock.Advance(TimeSpan.FromDays(13));
        Assert.Throws<ApplicationFailure>(() => store.Read(null));
    }
    [PgFact]
    public async Task RestingPersistedSnapshotSurvivesTrimAndKeepsItsClockUntilRetentionEnds()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        var repository = new PublicSnapshotRepository(new AccountDatabase(user.Connection));
        var input = new JsonObject { ["section"] = null, ["retention_test"] = user.Id }.ToJsonString();
        try
        {
            var id = jobs.Enqueue(user.Id, "market.snapshot", input, DateTime.UtcNow.AddMinutes(1));
            var claim = jobs.Claim("resting-global-snapshot", ["market.snapshot"])!;
            var data = new JsonObject { ["indices"] = new JsonArray(new JsonObject { ["symbol"] = "000300" }), ["fund_rankings"] = new JsonArray(), ["sector_rankings"] = new JsonArray(), ["observed_at"] = "2026-10-16T15:00:00+08:00", ["stale"] = false, ["unavailable"] = new JsonArray() };
            Assert.True(jobs.Finish(user.Id, id, claim.Attempt, claim.OwnerToken, data.ToJsonString(), null));
            user.Execute("UPDATE public_compute_snapshots SET completed_at=now()-interval '8 days',expires_at=now()-interval '8 days' WHERE input_json->>'retention_test'=$1", user.Id);
            await repository.TrimAsync();
            var saved = await repository.ReadAsync("market.snapshot", input);
            Assert.NotNull(saved);
            Assert.False(saved.Fresh);
            Assert.Equal("2026-10-16T15:00:00+08:00", JsonNode.Parse(saved.ResultJson)!["observed_at"]!.GetValue<string>());
            user.Execute("UPDATE public_compute_snapshots SET completed_at=now()-interval '15 days',expires_at=now()-interval '15 days' WHERE input_json->>'retention_test'=$1", user.Id);
            await repository.TrimAsync();
            Assert.Null(await repository.ReadAsync("market.snapshot", input));
        }
        finally { user.Execute("DELETE FROM public_compute_snapshots WHERE input_json->>'retention_test'=$1", user.Id); }
    }
}
