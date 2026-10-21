using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Errors;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class LedgerCsvImportTests
{
    [Fact]
    public void ExactGross_DoesNotAcceptDecimalMultiplicationRounding()
    {
        var trade=new LedgerCsvInput.Trade("510300","fund","buy","9999999999999999999.00000001","1.000001","0","2026-09-03T10:00:00+08:00","precision");
        Assert.Throws<ApplicationFailure>(()=>LedgerCsvInput.Validate(new([trade],"confirmed statement")));
    }
    private const string PreviewRoute = "/api/v1/ledger/replay/batch-preview";
    private const string ConfirmRoute = "/api/v1/ledger/replay/confirm";
    private static object Trade(string id, string quantity="10", string when="2026-09-03T10:00:00+08:00") => new { symbol="510300", asset_type="fund", action="buy", quantity, price="1", fees="0.5", occurred_at=when, external_id=id, row=2 };
    private static WebApplicationFactory<Program> Factory(PgTestUser user)
    { Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",user.Connection); return new WebApplicationFactory<Program>().WithWebHostBuilder(builder=>builder.ConfigureAppConfiguration((_,config)=>config.AddInMemoryCollection(new Dictionary<string,string?>{["QUANT_WORKER_SECRET"]="synthetic-ledger-csv-worker-secret-123"}))); }
    private static async Task<JsonObject> Ready(HttpClient client, PgTestUser user, string route, object body,int expectedTrades=2)
    {
        var pending=client.PostAsJsonAsync(route,body); var jobs=new ComputeJobRepository(user.Connection);
        ComputeJob? claim=null;
        for(var i=0;i<150 && claim is null && !pending.IsCompleted;i++) { claim=jobs.Claim("ledger-csv-fixture",["ledger.replay"]); if(claim is null)await Task.Delay(20); }
        Assert.NotNull(claim);
        var trades=JsonNode.Parse(claim.InputJson)!["transactions"]!.AsArray(); Assert.Equal(expectedTrades,trades.Count);
        var result=new JsonObject { ["status"]="ready",["blockers"]=new JsonArray(),["positions"]=new JsonArray(JsonNode.Parse("{\"symbol\":\"CNY\",\"asset_type\":\"cash\",\"quantity\":\"9979\",\"average_cost\":\"1\",\"market_value\":\"9979\",\"as_of_date\":\"2026-09-05\",\"updated_at\":\"2026-10-08T10:00:00+08:00\"}"),JsonNode.Parse("{\"symbol\":\"510300\",\"asset_type\":\"fund\",\"quantity\":\"20\",\"average_cost\":\"1.05\",\"market_value\":\"20\",\"as_of_date\":\"2026-09-05\",\"updated_at\":\"2026-10-08T10:00:00+08:00\"}")),["valuations"]=new JsonArray(),["transaction_audits"]=new JsonArray(trades.Select(t=>(JsonNode)new JsonObject { ["source"]="historical-manual",["external_id"]=t!["external_id"]!.DeepClone(),["before_json"]="{}",["after_json"]="{}" }).ToArray()) };
        Assert.True(jobs.Finish(user.Id,claim.JobId,claim.Attempt,claim.OwnerToken,result.ToJsonString(),null));
        var response=await pending; Assert.Equal(HttpStatusCode.OK,response.StatusCode);return (await response.Content.ReadFromJsonAsync<JsonObject>())!;
    }
    [PgFact]
    public async Task BatchCommit_IsAtomic_Owned_Idempotent_AndDuplicateImportIsReadonly()
    {
        using var user=new PgTestUser();user.SeedLedger();using var other=new PgTestUser();using var factory=Factory(user);
        using var client=factory.CreateTestClient();using var foreign=factory.CreateClient();PgTestUser.AttachSession(client,user.Connection,user.Id);PgTestUser.AttachSession(foreign,other.Connection,other.Id);
        var body=new { trades=new[]{Trade("csv-a"),Trade("csv-b",when:"2026-09-05T10:00:00+08:00")},reason="verified statement" };
        var preview=await Ready(client,user,PreviewRoute,body); Assert.Equal(0L,user.Query("SELECT count(*) FROM transactions WHERE user_id=$1",user.Id));
        var id=preview["preview_id"]!.GetValue<string>();var confirmation=new {preview_id=id,confirmed=true};
        Assert.Equal(HttpStatusCode.Conflict,(await foreign.PostAsJsonAsync(ConfirmRoute,confirmation)).StatusCode);
        Assert.Equal(HttpStatusCode.UnprocessableEntity,(await client.PostAsJsonAsync(ConfirmRoute,new {preview_id=id,confirmed=false})).StatusCode);
        // Fail the second insert; PostgreSQL must roll back the first insert and preview consumption.
        user.Execute("CREATE FUNCTION csv_fixture_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.user_id='"+user.Id+"' AND NEW.external_id='csv-b' THEN RAISE EXCEPTION 'synthetic rollback'; END IF; RETURN NEW; END $$");
        user.Execute("CREATE TRIGGER csv_fixture_fail BEFORE INSERT ON transactions FOR EACH ROW EXECUTE FUNCTION csv_fixture_fail()");
        try { var pending=client.PostAsJsonAsync(ConfirmRoute,confirmation); await FinishConfirmation(user,pending);Assert.Equal(HttpStatusCode.ServiceUnavailable,(await pending).StatusCode);Assert.Equal(0L,user.Query("SELECT count(*) FROM transactions WHERE user_id=$1",user.Id));Assert.Equal(10000m,user.Query("SELECT quantity FROM positions WHERE user_id=$1",user.Id)); }
        finally {user.Execute("DROP TRIGGER csv_fixture_fail ON transactions");user.Execute("DROP FUNCTION csv_fixture_fail()");}
        var recorded=await Ready(client,user,ConfirmRoute,confirmation);Assert.Equal(2,recorded["transaction_ids"]!.AsArray().Count);
        var repeated=(await (await client.PostAsJsonAsync(ConfirmRoute,confirmation)).Content.ReadFromJsonAsync<JsonObject>())!;Assert.True(JsonNode.DeepEquals(recorded,repeated));
        Assert.Equal(2L,user.Query("SELECT count(*) FROM transactions WHERE user_id=$1",user.Id));Assert.Equal(2L,user.Query("SELECT count(*) FROM audit_log WHERE user_id=$1 AND entity_type='transaction'",user.Id));
        var duplicate=(await (await client.PostAsJsonAsync(PreviewRoute,body)).Content.ReadFromJsonAsync<JsonObject>())!;Assert.Equal("already_recorded",duplicate["status"]!.GetValue<string>());Assert.Null(duplicate["preview_id"]);
        var conflict=(await (await client.PostAsJsonAsync(PreviewRoute,new {trades=new[]{Trade("csv-a","11")},reason="statement"})).Content.ReadFromJsonAsync<JsonObject>())!;Assert.Equal("blocked",conflict["status"]!.GetValue<string>());
        Assert.Equal(2L,user.Query("SELECT count(*) FROM transactions WHERE user_id=$1",user.Id));
        var mixed=await Ready(client,user,PreviewRoute,new {trades=new[]{Trade("csv-a"),Trade("csv-b",when:"2026-09-05T10:00:00+08:00"),Trade("csv-c",when:"2026-09-06T10:00:00+08:00")},reason="new statement"},1);
        Assert.Equal(1,mixed["new_count"]!.GetValue<int>());Assert.Equal(2,mixed["duplicate_count"]!.GetValue<int>());
        var mixedId=mixed["preview_id"]!.GetValue<string>();
        var mixedRecorded=await Ready(client,user,ConfirmRoute,new {preview_id=mixedId,confirmed=true},1);
        var receipt=(await client.GetFromJsonAsync<JsonObject>($"/api/v1/ledger/replay/{mixedId}/receipt"))!;
        var receiptRows=receipt["rows"]!.AsArray();Assert.Equal(3,receiptRows.Count);
        Assert.Equal("duplicate",receiptRows[0]!["status"]!.GetValue<string>());
        Assert.True(JsonNode.DeepEquals(recorded["transaction_ids"]![0],receiptRows[0]!["transaction_id"]));
        Assert.True(JsonNode.DeepEquals(mixedRecorded["transaction_ids"]![0],receiptRows[2]!["transaction_id"]));
        Assert.Equal(0L,(await foreign.GetFromJsonAsync<JsonObject>("/api/v1/ledger/replay/history"))!["total"]!.GetValue<long>());
    }
    [PgFact]
    public async Task SavedBatchReceipts_AreOwned_Readonly_Paginated_AndSurvivePreviewExpiry()
    {
        using var user=new PgTestUser();using var other=new PgTestUser();using var factory=Factory(user);
        using var client=factory.CreateTestClient();using var foreign=factory.CreateClient();
        PgTestUser.AttachSession(client,user.Connection,user.Id);PgTestUser.AttachSession(foreign,other.Connection,other.Id);
        var plan="{\"transactions\":[{}],\"reason\":\"verified old statement\",\"new_count\":1,\"duplicate_count\":1,\"rows\":[{\"row\":2,\"external_id\":\"old\",\"status\":\"duplicate\",\"transaction_id\":10,\"message\":\"skip\"},{\"row\":3,\"external_id\":\"new\",\"status\":\"new\",\"transaction_id\":null,\"message\":\"pending\"}]}";
        foreach(var id in new[]{"saved-a","saved-b","saved-c","pending","single"})
            user.Execute("INSERT INTO ledger_replay_previews(user_id,preview_id,source_fingerprint,plan_json,result_json,created_at,expires_at,consumed_at,transaction_id) VALUES($1,$2,'frozen',$3::json,'{\"transaction_ids\":[11]}'::json,now(),now()-interval '1 day',CASE WHEN $2='pending' THEN NULL ELSE now() END,11)",user.Id,id,id=="single"?"{\"transaction\":{},\"reason\":\"single\"}":plan);
        var first=(await client.GetFromJsonAsync<JsonObject>("/api/v1/ledger/replay/batch-history?limit=2"))!;
        Assert.Equal(3L,first["total"]!.GetValue<long>());Assert.Equal(2,first["items"]!.AsArray().Count);
        Assert.Null(first["items"]![0]!["plan_json"]);
        var second=(await client.GetFromJsonAsync<JsonObject>("/api/v1/ledger/replay/batch-history?page=2&limit=2"))!;
        Assert.Single(second["items"]!.AsArray());
        var saved=(await client.GetFromJsonAsync<JsonObject>("/api/v1/ledger/replay/saved-a/receipt"))!;
        Assert.Equal("verified old statement",saved["reason"]!.GetValue<string>());
        Assert.Equal(11L,saved["rows"]![1]!["transaction_id"]!.GetValue<long>());
        Assert.Equal("本批已入账",saved["rows"]![1]!["message"]!.GetValue<string>());
        foreach(var id in new[]{"saved-a","pending","single","missing"})
            Assert.Equal(HttpStatusCode.NotFound,(await foreign.GetAsync($"/api/v1/ledger/replay/{id}/receipt")).StatusCode);
        foreach(var id in new[]{"pending","single","missing"})
            Assert.Equal(HttpStatusCode.NotFound,(await client.GetAsync($"/api/v1/ledger/replay/{id}/receipt")).StatusCode);
        Assert.Equal(0L,(await foreign.GetFromJsonAsync<JsonObject>("/api/v1/ledger/replay/batch-history"))!["total"]!.GetValue<long>());
        Assert.Equal(HttpStatusCode.UnprocessableEntity,(await client.GetAsync("/api/v1/ledger/replay/batch-history?page=0")).StatusCode);
        Assert.Equal(0L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1",user.Id));
        Assert.Equal(0L,user.Query("SELECT count(*) FROM transactions WHERE user_id=$1",user.Id));
        Assert.Equal(5L,user.Query("SELECT count(*) FROM ledger_replay_previews WHERE user_id=$1",user.Id));
    }
    private static async Task FinishConfirmation(PgTestUser user,Task<HttpResponseMessage> pending)
    {
        // The same worker boundary as Ready, with the known deterministic result.
        var jobs=new ComputeJobRepository(user.Connection);ComputeJob? claim=null;
        for(var i=0;i<150 && claim is null && !pending.IsCompleted;i++){claim=jobs.Claim("ledger-csv-fixture",["ledger.replay"]);if(claim is null)await Task.Delay(20);}Assert.True(claim is not null, pending.IsCompleted ? await (await pending).Content.ReadAsStringAsync() : "worker job not queued");
        var result=(string)user.Query("SELECT result_json::text FROM ledger_replay_previews WHERE user_id=$1 ORDER BY created_at DESC LIMIT 1",user.Id)!;
        Assert.True(jobs.Finish(user.Id,claim.JobId,claim.Attempt,claim.OwnerToken,result,null));
    }
    [PgFact]
    public async Task BatchValidation_RejectsPrecision_MissingTimezone_UnknownInstrument_AndDuplicateIds_BeforeWorker()
    {
        using var user=new PgTestUser();user.SeedLedger();using var factory=Factory(user);using var client=factory.CreateTestClient();PgTestUser.AttachSession(client,user.Connection,user.Id);
        foreach(var trades in new[]{Array.Empty<object>(),new[]{Trade("same"),Trade("same",when:"2026-09-05T10:00:00+08:00")},new[]{Trade("bad","0.123456789")},new[]{Trade("bad",when:"2026-09-03T10:00:00")},new[]{Trade("bad","1e3")}})
            Assert.Equal(HttpStatusCode.UnprocessableEntity,(await client.PostAsJsonAsync(PreviewRoute,new {trades,reason="statement"})).StatusCode);
        Assert.Equal(0L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1",user.Id));
        var unknown=JsonNode.Parse(System.Text.Json.JsonSerializer.Serialize(Trade("unknown")))!;unknown["symbol"]="999999";
        var response=await client.PostAsJsonAsync(PreviewRoute,new {trades=new[]{unknown},reason="statement"});Assert.Equal(HttpStatusCode.UnprocessableEntity,response.StatusCode);Assert.Contains("代码尚未登记",(await response.Content.ReadFromJsonAsync<JsonObject>())!["message"]!.GetValue<string>());
        Assert.Equal(0L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1",user.Id));
    }
    [PgFact]
    public async Task StaleAndExpiredPreviews_BlockBeforeCompute_AndKeepEntireLedger()
    {
        using var user=new PgTestUser();user.SeedLedger();using var factory=Factory(user);using var client=factory.CreateTestClient();PgTestUser.AttachSession(client,user.Connection,user.Id);
        var body=new {trades=new[]{Trade("csv-a"),Trade("csv-b",when:"2026-09-05T10:00:00+08:00")},reason="statement"};
        var first=await Ready(client,user,PreviewRoute,body);user.Execute("UPDATE positions SET quantity=10001 WHERE user_id=$1",user.Id);
        Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync(ConfirmRoute,new {preview_id=first["preview_id"]!.GetValue<string>(),confirmed=true})).StatusCode);
        var second=await Ready(client,user,PreviewRoute,body);user.Execute("UPDATE ledger_replay_previews SET expires_at=now()-interval '1 minute' WHERE user_id=$1",user.Id);
        Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync(ConfirmRoute,new {preview_id=second["preview_id"]!.GetValue<string>(),confirmed=true})).StatusCode);
        Assert.Equal(0L,user.Query("SELECT count(*) FROM transactions WHERE user_id=$1",user.Id));Assert.Equal(10001m,user.Query("SELECT quantity FROM positions WHERE user_id=$1",user.Id));Assert.Equal(2L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1",user.Id));
    }
}
