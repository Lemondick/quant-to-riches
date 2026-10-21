using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Market;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class FundCatalogApiTests
{
    [Fact]
    public async Task CatalogAndCategoriesRequireRealAccountAuthentication()
    {
        using var factory=new WebApplicationFactory<Program>();using var client=factory.CreateTestClient();
        foreach(var path in new[]{"/api/v1/funds/catalog","/api/v1/funds/categories"})
            Assert.Equal(HttpStatusCode.Unauthorized,(await client.GetAsync(path)).StatusCode);
    }
    [Fact]
    public void MoneyMetricsMissingNavAndRealZeroAreDistinct()
    {
        JsonObject Row(string type)=>new(){["asset_class"]=type,["fund_type"]="股票型"};
        var detail=JsonNode.Parse("""{"points":[{"nav_date":"2026-10-19","unit_nav":"1.2300","daily_return":"0","source":"eastmoney"}],"fetched_at":"2026-10-20T08:00:00Z"}""")!.AsObject();
        var now=DateTimeOffset.Parse("2026-10-20T16:00:00Z");
        var normal=Row("股票型");FundCatalogService.ApplyNav(normal,detail,now,MarketCalendars.Empty);
        Assert.Equal("1.2300",normal["unit_nav"]!.GetValue<string>());Assert.Equal("0",normal["daily_return"]!.GetValue<string>());
        Assert.Equal("2026-10-19",normal["nav_date"]!.GetValue<string>());
        var inconsistent=detail.DeepClone().AsObject();inconsistent["points"]![0]!["validation_errors"]=new JsonArray("daily_return_inconsistent");
        var held=Row("股票型");FundCatalogService.ApplyNav(held,inconsistent,now,MarketCalendars.Empty);Assert.Null(held["daily_return"]);Assert.Equal("1.2300",held["unit_nav"]!.GetValue<string>());
        var money=Row("货币型");FundCatalogService.ApplyNav(money,detail,now,MarketCalendars.Empty);
        Assert.Null(money["unit_nav"]);Assert.Equal("money_fund_metric_not_verified",money["nav_reason"]!.GetValue<string>());
        var conflict=Row("未知");conflict["fund_type"]="货币型-普通货币";FundCatalogService.ApplyNav(conflict,detail,now,MarketCalendars.Empty);Assert.Null(conflict["unit_nav"]);
        var missing=Row("股票型");FundCatalogService.ApplyNav(missing,null,now,MarketCalendars.Empty);Assert.Null(missing["unit_nav"]);Assert.Null(missing["daily_return"]);
        var qdii=detail.DeepClone().AsObject();qdii["nav_freshness_policy"]="qdii_t3";qdii["points"]![0]!["nav_date"]="2026-10-15";
        var foreign=Row("股票型");FundCatalogService.ApplyNav(foreign,qdii,now,MarketCalendars.Builtin);
        Assert.Equal("valid",foreign["nav_quality_status"]!.GetValue<string>());Assert.Equal("qdii_t3",foreign["nav_freshness_policy"]!.GetValue<string>());
    }
    [PgFact]
    public async Task CatalogFiltersShareOnlyPublicFactsAndNeverCreatePerFundCrawlers()
    {
        using var user=new PgTestUser();using var other=new PgTestUser();
        var previous=Environment.GetEnvironmentVariable("QUANT_PG_CONNECTION");Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",user.Connection);
        var key="catalog-api-"+user.Id;
        var directory=new JsonObject{["schema_version"]="fund-directory.v1",["source"]="eastmoney",["source_time"]=null,["fetched_at"]=DateTimeOffset.UtcNow.ToString("O"),["quality_status"]="unknown_time",["stale"]=false,["items"]=new JsonArray(
            new JsonObject{["symbol"]="023530",["name"]="明确测试航空基金A",["fund_type"]="指数型-股票"},
            new JsonObject{["symbol"]="110020",["name"]="明确测试基金C",["fund_type"]="债券型-长债"})};
        try
        {
            user.Execute("INSERT INTO public_compute_snapshots(kind,input_hash,input_json,result_json,completed_at,expires_at) VALUES('fund.catalog',$1,'{}',$2::jsonb,now(),now()+interval '6 hours')",key,directory.ToJsonString());
            using var factory=new WebApplicationFactory<Program>().WithWebHostBuilder(builder=>builder.ConfigureAppConfiguration((_,config)=>config.AddInMemoryCollection(new Dictionary<string,string?>{["QUANT_PUBLIC_REFRESH_ENABLED"]="false"})));
            using var client=factory.CreateTestClient();PgTestUser.AttachSession(client,user.Connection,user.Id);
            foreach(var initial in new[]{"/api/v1/funds/catalog","/api/v1/funds/catalog?q=","/api/v1/funds/catalog?q=%20%20"})
                Assert.Equal(HttpStatusCode.OK,(await client.GetAsync(initial)).StatusCode);
            var path="/api/v1/funds/catalog?q=023&asset_class="+Uri.EscapeDataString("股票型")+"&strategy_tags="+Uri.EscapeDataString("指数策略")+"&share_class=__unknown&page_size=1";
            var result=await client.GetFromJsonAsync<JsonObject>(path);
            Assert.Equal(1,result!["total"]!.GetValue<int>());Assert.Equal("023530",result["items"]![0]!["symbol"]!.GetValue<string>());
            Assert.Null(result["items"]![0]!["unit_nav"]);Assert.Null(result["source_time"]);
            var categories=await client.GetFromJsonAsync<JsonObject>("/api/v1/funds/categories");Assert.NotNull(categories!["dimensions"]!["asset_class"]);
            // The complete directory is retained once in the service, not parsed for every page/account.
            user.Execute("DELETE FROM public_compute_snapshots WHERE input_hash=$1",key);
            using var otherClient=factory.CreateTestClient();PgTestUser.AttachSession(otherClient,user.Connection,other.Id);
            var same=await otherClient.GetFromJsonAsync<JsonObject>(path);Assert.True(JsonNode.DeepEquals(result["items"],same!["items"]));
            Assert.Equal(0L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id IN ($1,$2)",user.Id,other.Id));
            foreach(var invalid in new[]{"page=0","page_size=101","asset_class="+new string('x',101)})Assert.Equal(HttpStatusCode.UnprocessableEntity,(await client.GetAsync("/api/v1/funds/catalog?"+invalid)).StatusCode);
        }
        finally{user.Execute("DELETE FROM public_compute_snapshots WHERE input_hash=$1",key);Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",previous);}
    }
    [PgFact]
    public async Task StaleDirectoryDoesNotQueueAgainAfterRefreshCompletesWhileMemoryIsRetained()
    {
        using var user=new PgTestUser();using var other=new PgTestUser();
        var previous=Environment.GetEnvironmentVariable("QUANT_PG_CONNECTION");Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",user.Connection);
        var key="catalog-refresh-"+user.Id;
        var directory=new JsonObject{["schema_version"]="fund-directory.v1",["source"]="eastmoney",["source_time"]=null,
            ["fetched_at"]=DateTimeOffset.UtcNow.AddHours(-7).ToString("O"),["quality_status"]="unknown_time",["stale"]=false,["refresh_test"]=user.Id,
            ["items"]=new JsonArray(new JsonObject{["symbol"]="023530",["name"]="Explicit refresh regression fixture",["fund_type"]="指数型-股票"})};
        try
        {
            user.Execute("INSERT INTO public_compute_snapshots(kind,input_hash,input_json,result_json,completed_at,expires_at) VALUES('fund.catalog',$1,'{}',$2::jsonb,now()-interval '7 hours',now()-interval '1 hour')",key,directory.ToJsonString());
            using var factory=new WebApplicationFactory<Program>().WithWebHostBuilder(builder=>builder.ConfigureAppConfiguration((_,config)=>config.AddInMemoryCollection(new Dictionary<string,string?>{
                ["QUANT_PUBLIC_REFRESH_ENABLED"]="false",["QUANT_WORKER_SECRET"]="synthetic-catalog-worker-secret-123456"})));
            using var client=factory.CreateTestClient();PgTestUser.AttachSession(client,user.Connection,user.Id);
            var initial=await client.GetFromJsonAsync<JsonObject>("/api/v1/funds/categories");Assert.True(initial!["stale"]!.GetValue<bool>());
            Assert.Equal(1L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id IN ($1,$2) AND kind='fund.catalog'",user.Id,other.Id));
            var jobs=new ComputeJobRepository(user.Connection);var claim=jobs.Claim("catalog-refresh-regression",["fund.catalog"])!;
            Assert.Equal(user.Id,claim.UserId);Assert.True(JsonNode.Parse(claim.InputJson)!["_refresh"]!.GetValue<bool>());
            directory["fetched_at"]=DateTimeOffset.UtcNow.ToString("O");
            Assert.True(jobs.Finish(user.Id,claim.JobId,claim.Attempt,claim.OwnerToken,directory.ToJsonString(),null));
            Assert.Equal(1L,user.Query("SELECT count(*) FROM public_compute_snapshots WHERE kind='fund.catalog' AND input_hash<>$1 AND result_json->>'refresh_test'=$2",key,user.Id));
            using var otherClient=factory.CreateTestClient();PgTestUser.AttachSession(otherClient,user.Connection,other.Id);
            // The singleton still serves its observed stale directory for 60 seconds, across accounts.
            foreach(var reader in new[]{client,otherClient,client,otherClient})
            {
                var retained=await reader.GetFromJsonAsync<JsonObject>("/api/v1/funds/categories");
                Assert.Equal(initial["fetched_at"]!.GetValue<string>(),retained!["fetched_at"]!.GetValue<string>());
                Assert.True(retained["stale"]!.GetValue<bool>());
            }
            Assert.Equal(1L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id IN ($1,$2) AND kind='fund.catalog'",user.Id,other.Id));
        }
        finally
        {
            user.Execute("DELETE FROM public_compute_snapshots WHERE kind='fund.catalog' AND (input_hash=$1 OR result_json->>'refresh_test'=$2)",key,user.Id);
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",previous);
        }
    }
    [PgFact]
    public async Task UnavailableDirectoryQueuesOnePublicTaskWithoutBlockingOnCrawler()
    {
        using var user=new PgTestUser();var previous=Environment.GetEnvironmentVariable("QUANT_PG_CONNECTION");
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",user.Connection);
        try
        {
            using var factory=new WebApplicationFactory<Program>().WithWebHostBuilder(builder=>builder.ConfigureAppConfiguration((_,config)=>config.AddInMemoryCollection(new Dictionary<string,string?>{
                ["QUANT_PUBLIC_REFRESH_ENABLED"]="false",["QUANT_WORKER_SECRET"]="synthetic-catalog-worker-secret-123456"})));
            using var client=factory.CreateTestClient();PgTestUser.AttachSession(client,user.Connection,user.Id);
            foreach(var path in new[]{"/api/v1/funds/catalog","/api/v1/funds/categories"})Assert.Equal(HttpStatusCode.ServiceUnavailable,(await client.GetAsync(path)).StatusCode);
            Assert.Equal(1L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND kind='fund.catalog'",user.Id));
            var input=JsonNode.Parse((string)user.Query("SELECT input_json::text FROM compute_jobs WHERE user_id=$1 AND kind='fund.catalog'",user.Id)!)!.AsObject();
            Assert.All(input,pair=>Assert.Equal("_refresh",pair.Key));
        }
        finally{Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",previous);}
    }
}
