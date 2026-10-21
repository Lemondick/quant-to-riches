using System.Text.Json.Nodes;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class FundCatalogStorageTests
{
    [Fact]
    public void CatalogIsAnIndependentBoundedPublicOperation()
    {
        Assert.Contains("fund.catalog", ComputeJobRepository.IndependentPublicKinds);
        Assert.Contains("fund.catalog", ComputeJobRepository.AllowedKinds);
        Assert.Equal(21600, ComputeJobRepository.PublicCacheSeconds("fund.catalog"));
    }

    private static void Complete(PgTestUser user, string kind, string input, JsonObject result)
    {
        var jobs = new ComputeJobRepository(user.Connection);
        var id = jobs.Enqueue(user.Id, kind, input, DateTime.UtcNow.AddMinutes(2), reuseCompleted:false);
        var claim = jobs.Claim("catalog-storage", [kind])!;
        Assert.Equal(id, claim.JobId);
        Assert.True(jobs.Finish(user.Id, id, claim.Attempt, claim.OwnerToken, result.ToJsonString(), null));
    }

    [PgFact]
    public void FailedOrFallbackCatalogDoesNotBecomeASixHourCompletedCacheHit()
    {
        using var user=new PgTestUser();var jobs=new ComputeJobRepository(user.Connection);
        foreach(var mode in new[]{"empty","invalid","fallback"})
        {
            var input=new JsonObject{["recovery_test"]=user.Id,["mode"]=mode}.ToJsonString();
            var bad=new JsonObject{["schema_version"]="fund-directory.v1",["source"]="eastmoney",["source_time"]=null,["fetched_at"]=DateTimeOffset.UtcNow.ToString("O"),
                ["items"]=mode=="empty"?new JsonArray():new JsonArray(new JsonObject{["symbol"]="000001",["name"]="Explicit recovery fixture",["fund_type"]=null}),
                ["stale"]=mode=="fallback",["quality_status"]=mode=="invalid"?"invalid":"unknown_time"};
            Complete(user,"fund.catalog",input,bad);
            var id=jobs.Enqueue(user.Id,"fund.catalog",input,DateTime.UtcNow.AddMinutes(1));
            Assert.Equal("queued",jobs.Read(user.Id,id).Status);
            Assert.Equal(id,jobs.Enqueue(user.Id,"fund.catalog",input,DateTime.UtcNow.AddMinutes(1)));
            user.Execute("UPDATE compute_jobs SET status='cancelled',completed_at=now() WHERE user_id=$1 AND job_id=$2",user.Id,id);
        }
    }

    [PgFact]
    public async Task QualifiedCatalogIsSharedButEmptyPrivateAndFallbackResultsAreNotPublished()
    {
        using var user = new PgTestUser();
        var input = new JsonObject { ["storage_test"] = user.Id }.ToJsonString();
        var repository = new PublicSnapshotRepository(new AccountDatabase(user.Connection));
        var result = JsonNode.Parse("""{"schema_version":"fund-directory.v1","source":"eastmoney","source_time":null,"fetched_at":"2026-10-20T16:00:00Z","quality_status":"unknown_time","items":[{"symbol":"000001","name":"明确测试夹具","fund_type":null}],"unavailable":[],"stale":false}""")!.AsObject();
        try
        {
            Complete(user, "fund.catalog", input, result);
            Assert.NotNull(await repository.ReadAsync("fund.catalog", input));
            user.Execute("DELETE FROM public_compute_snapshots WHERE kind='fund.catalog' AND input_json=$1::jsonb", input);
            foreach(var mode in new[]{"empty","private","fallback","invalid"})
            {
                var bad=result.DeepClone().AsObject();
                if(mode=="empty")bad["items"]=new JsonArray();
                // 隐私门控在结果顶层（触发器检查 result_json ?| [user_id, snapshot, credentials]），故 user_id 标在顶层。
                if(mode=="private")bad["user_id"]="private-account";
                if(mode=="fallback")bad["stale"]=true;
                if(mode=="invalid")bad["quality_status"]="invalid";
                Complete(user,"fund.catalog",input,bad);
                Assert.Null(await repository.ReadAsync("fund.catalog",input));
            }
        }
        finally { user.Execute("DELETE FROM public_compute_snapshots WHERE kind='fund.catalog' AND input_json=$1::jsonb", input); }
    }

    [PgFact]
    public async Task QualifiedDelayedHistoryCanBeSharedWithoutRegressingDatesOrPublishingFallbacks()
    {
        using var user = new PgTestUser();
        var input=new JsonObject{["storage_test"]=user.Id}.ToJsonString();
        var repository=new PublicSnapshotRepository(new AccountDatabase(user.Connection));
        JsonObject Data(string date)=>new(){["symbol"]="N225",["name"]="fixture",["source"]="synthetic",["observed_at"]="2026-10-20T16:00:00Z",["unavailable"]=new JsonArray(),["quality_status"]="stale",["stale"]=true,["items"]=new JsonArray(new JsonObject{["date"]=date,["close"]="100"})};
        try
        {
            Complete(user,"market.history",input,Data("2026-10-16"));
            Complete(user,"market.history",input,Data("2026-10-19"));
            Assert.Contains("2026-10-19",(await repository.ReadAsync("market.history",input))!.ResultJson);
            foreach(var mode in new[]{"older","empty","invalid","fallback"})
            {
                var bad=Data(mode=="older"?"2026-10-15":"2026-10-20");
                if(mode=="empty")bad["items"]=new JsonArray();
                if(mode=="invalid")bad["quality_status"]="invalid";
                if(mode=="fallback")bad["fallback_used"]=true;
                Complete(user,"market.history",input,bad);
                Assert.Contains("2026-10-19",(await repository.ReadAsync("market.history",input))!.ResultJson);
            }
        }
        finally { user.Execute("DELETE FROM public_compute_snapshots WHERE kind='market.history' AND input_json=$1::jsonb",input); }
    }
}
