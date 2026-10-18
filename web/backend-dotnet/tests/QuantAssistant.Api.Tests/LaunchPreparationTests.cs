using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class LaunchPreparationTests
{
    [PgFact]
    public async Task HistoricalReconciliationIsOwnedFrozenAndOnlyCurrentEvidenceCanBeResolved()
    {
        using var user=new PgTestUser();using var other=new PgTestUser();
        user.Execute("INSERT INTO portfolio_accounts(user_id,account_id,name,platform) VALUES($1,'account','合成账户','synthetic')",user.Id);
        user.Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date) VALUES($1,'CNY','cash',100,1,100,'2026-01-02')",user.Id);
        user.Execute("INSERT INTO account_positions(user_id,account_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date) VALUES($1,'account','CNY','cash',101,1,101,'2026-01-02')",user.Id);
        var repository=new ReconciliationRepository(new AccountDatabase(user.Connection));
        var saved=repository.Compare(user.Id,new(["account"],true,0,new(2026,1,2),new(2026,1,2),"=synthetic proof"));
        var id=saved["reconciliation_id"]!.ToString();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",user.Connection);
        using var factory=new WebApplicationFactory<Program>();using var client=factory.CreateClient();using var foreign=factory.CreateClient();using var anonymous=factory.CreateClient();
        PgTestUser.AttachSession(client,user.Connection,user.Id);PgTestUser.AttachSession(foreign,user.Connection,other.Id);
        var route="/api/v1/ledger/reconciliation/"+id;
        Assert.Equal(HttpStatusCode.Unauthorized,(await anonymous.GetAsync(route)).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound,(await foreign.GetAsync(route)).StatusCode);
        var read=await client.GetFromJsonAsync<JsonElement>(route);
        Assert.True(read.GetProperty("can_resolve").GetBoolean());
        Assert.Equal(1m,read.GetProperty("items")[0].GetProperty("quantity_delta").DecimalText());
        Assert.Equal(HttpStatusCode.OK,(await client.PostAsJsonAsync(route+"/resolve",new{resolution="explained",note="synthetic receipt checked"})).StatusCode);
        Assert.Equal("synthetic receipt checked",(await client.GetFromJsonAsync<JsonElement>(route)).GetProperty("resolution_note").GetString());
        user.Execute("UPDATE positions SET quantity=102,market_value=102 WHERE user_id=$1",user.Id);
        var stale=await client.GetFromJsonAsync<JsonElement>(route);
        Assert.False(stale.GetProperty("can_resolve").GetBoolean());
        Assert.Equal(1m,stale.GetProperty("items")[0].GetProperty("quantity_delta").DecimalText());
        Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync(route+"/resolve",new{resolution="explained",note="stale"})).StatusCode);
        Assert.Equal(0L,user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1",user.Id));
        Assert.Equal(102m,user.Query("SELECT quantity FROM positions WHERE user_id=$1",user.Id));
    }

    [PgFact]
    public async Task BillEvidenceRequiresOwnedUnknownCompletedCallExplicitProofAndIsIdempotent()
    {
        using var user=new PgTestUser();using var other=new PgTestUser();
        var database=new AccountDatabase(user.Connection);var costs=new ModelCostRepository(database);
        var id=costs.Reserve(user.Id,"unpriced",null,null);costs.Finish(user.Id,id,100,50);
        costs.Save(user.Id,new(0,Prices:new(){["unpriced"]=new(2,3)}));
        Assert.Null(costs.Read(user.Id)["items"]![0]!["cost"]);
        Assert.False(database.Read(sql=>ModelCostRepository.EvidenceReadinessFor(sql,user.Id))["passed"]!.GetValue<bool>());
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",user.Connection);
        using var factory=new WebApplicationFactory<Program>();using var client=factory.CreateClient();using var foreign=factory.CreateClient();
        PgTestUser.AttachSession(client,user.Connection,user.Id);PgTestUser.AttachSession(foreign,user.Connection,other.Id);
        var route="/api/v1/runtime/costs/"+id+"/bill-evidence";
        var proof=new{amount="0.12345678",currency="CNY",reference="synthetic bill line #1",confirmed=true};
        Assert.Equal(HttpStatusCode.UnprocessableEntity,(await client.PostAsJsonAsync(route,new{amount="0",currency="CNY",reference="",confirmed=false})).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound,(await foreign.PostAsJsonAsync(route,proof)).StatusCode);
        Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync(route,new{amount="0.1",currency="USD",reference="foreign currency",confirmed=true})).StatusCode);
        for(var attempt=0;attempt<2;attempt++) Assert.Equal(HttpStatusCode.OK,(await client.PostAsJsonAsync(route,proof)).StatusCode);
        var row=costs.Read(user.Id)["items"]![0]!;
        Assert.Equal("0.12345678",row["cost"]!.ToString());Assert.Equal("missing_price",row["bill_evidence_json"]!["original_unknown_reason"]!.ToString());
        Assert.Equal(100L,user.Query("SELECT input_tokens FROM model_call_costs WHERE user_id=$1 AND id=$2",user.Id,id));
        Assert.Equal(true,user.Query("SELECT prices_json IS NULL FROM model_call_costs WHERE user_id=$1 AND id=$2",user.Id,id));
        Assert.Equal(1L,user.Query("SELECT count(*) FROM audit_log WHERE user_id=$1 AND entity_type='model_bill_evidence'",user.Id));
        Assert.True(database.Read(sql=>ModelCostRepository.EvidenceReadinessFor(sql,user.Id))["passed"]!.GetValue<bool>());
        Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync(route,new{amount="0",currency="CNY",reference="overwrite",confirmed=true})).StatusCode);
        var pending=costs.Reserve(user.Id,"unpriced",null,null);
        Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync("/api/v1/runtime/costs/"+pending+"/bill-evidence",proof)).StatusCode);
    }
}
