using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class LinkedAttributionTests
{
    private static AttributionReportView SingleSector(string sector, decimal portfolio, decimal benchmark, int month)
    {
        var details = new List<AttributionDetail> { new() { SectorName=sector, PortfolioWeight=1, BenchmarkWeight=1, PortfolioReturn=portfolio, BenchmarkReturn=benchmark } };
        BrinsonModel.CalculateEffects(details);
        return new(new AttributionReport { UserId="synthetic", PortfolioSymbol="P", BenchmarkSymbol="B", Status="completed",
            StartDate=new(2026,month,1), EndDate=new(2026,month+1,1), TotalActiveReturn=portfolio-benchmark,
            AllocationEffect=0, SelectionEffect=portfolio-benchmark, InteractionEffect=0 }, details);
    }

    [Fact]
    public void ThreePeriods_PreserveEffectsOfDisappearingSectors()
    {
        var result=BrinsonLinking.Calculate([SingleSector("科技",.1m,.05m,1),SingleSector("金融",.2m,.1m,2),SingleSector("现金",-.5m,-.2m,3)]);
        Assert.Equal(-.34m,result.PortfolioReturn);
        Assert.Equal(-.076m,result.BenchmarkReturn);
        Assert.Equal(-.264m,result.ActiveReturn);
        Assert.Equal(.044m,result.Sectors.Single(s=>s.SectorName=="科技").Selection);
        Assert.Equal(.088m,result.Sectors.Single(s=>s.SectorName=="金融").Selection);
        Assert.Equal(-.396m,result.Sectors.Single(s=>s.SectorName=="现金").Selection);
        Assert.Equal(0,result.ReconciliationResidual);
    }
    [Fact]
    public void AnnualDailyLedgerPeriodsCanLinkBeyondSixtyAndRemainBounded()
    {
        var periods=Enumerable.Range(0,366).Select(index=>
        {
            var period=SingleSector("科技",.001m,0,1);
            period.Report.StartDate=new DateOnly(2026,1,1).AddDays(index);
            period.Report.EndDate=period.Report.StartDate.AddDays(1);
            period.Report.DataSource="native_ledger_period";
            return period;
        }).ToArray();
        var saved=BrinsonLinking.Calculate(periods);
        Assert.True(saved.PortfolioReturn>.44m&&saved.PortfolioReturn<.45m);
        Assert.True(Math.Abs(saved.ActiveReturn-saved.Selection)<.000000000001m);
        Assert.Throws<ArgumentException>(()=>BrinsonLinking.Calculate([..periods,periods[0]]));
    }

    [Fact]
    public void TotalLossAndZeroActiveReturn_AreDefined_AndExtremeCompoundingIsRejected()
    {
        var loss=BrinsonLinking.Calculate([SingleSector("科技",-1,0,1),SingleSector("科技",5,0,2)]);
        Assert.Equal(-1,loss.PortfolioReturn); Assert.Equal(-1,loss.Selection); Assert.Equal(0,loss.ReconciliationResidual);
        var zero=BrinsonLinking.Calculate([SingleSector("科技",-1,-1,1),SingleSector("金融",0,0,2)]);
        Assert.Equal(0,zero.ActiveReturn); Assert.Equal(0,zero.ReconciliationResidual);
        Assert.Throws<ArgumentException>(()=>BrinsonLinking.Calculate([SingleSector("科技",1000000,0,1),SingleSector("科技",1000000,0,2)]));
    }

    private const string Route = "/api/v1/attribution/reports/link";
    private static AttributionReportView Seed(PgTestUser user, DateOnly start, DateOnly end, bool second = false)
    {
        var details = new List<AttributionDetail>
        {
            new() { SectorName="科技", PortfolioWeight=second ? .2m : .5m, BenchmarkWeight=.4m, PortfolioReturn=second ? -.1m : .1m, BenchmarkReturn=second ? .02m : .08m },
            new() { SectorName="金融", PortfolioWeight=second ? .8m : .5m, BenchmarkWeight=.6m, PortfolioReturn=second ? .3m : .05m, BenchmarkReturn=second ? .1m : .04m }
        };
        BrinsonModel.CalculateEffects(details);
        var report = new AttributionReport
        {
            Id=Guid.NewGuid().ToString("N"), UserId=user.Id, PortfolioSymbol="P", BenchmarkSymbol="B", StartDate=start, EndDate=end, Status="completed",
            TotalActiveReturn=details.Sum(d=>d.PortfolioWeight*d.PortfolioReturn-d.BenchmarkWeight*d.BenchmarkReturn),
            AllocationEffect=details.Sum(d=>d.Allocation), SelectionEffect=details.Sum(d=>d.Selection), InteractionEffect=details.Sum(d=>d.Interaction)
        };
        foreach(var detail in details) detail.ReportId=report.Id;
        new AttributionRepository(user.Connection).CreateReport(report,details);
        return new(report,details);
    }

    [PgFact]
    public async Task LinksChangingWeights_CompoundsReturns_FreezesSources_AndRetriesWithoutDuplicateWrites()
    {
        using var user=new PgTestUser();
        var first=Seed(user,new(2026,1,2),new(2026,1,15));
        var second=Seed(user,new(2026,1,15),new(2026,1,30),true);
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",user.Connection);
        using var factory=new WebApplicationFactory<Program>();
        using var client=factory.CreateClient();
        PgTestUser.AttachSession(client,user.Connection,user.Id);
        var id=Guid.NewGuid().ToString("N");
        var body=new {report_id=id,report_ids=new[]{second.Report.Id,first.Report.Id}};
        var response=await client.PostAsJsonAsync(Route,body);
        Assert.Equal(HttpStatusCode.Created,response.StatusCode);
        var view=(await response.Content.ReadFromJsonAsync<JsonElement>());
        var saved=view.GetProperty("report");
        Assert.Equal("native_linked_brinson",saved.GetProperty("data_source").GetString());
        Assert.Equal(.183692m,saved.GetProperty("total_active_return").DecimalText());
        Assert.Equal(.021472m,saved.GetProperty("allocation_effect").DecimalText());
        Assert.Equal(.092352m,saved.GetProperty("selection_effect").DecimalText());
        Assert.Equal(.069868m,saved.GetProperty("interaction_effect").DecimalText());
        var source=saved.GetProperty("source_json");
        Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync($"/api/v1/attribution/reports/{id}/calculate",new{details=first.Details.Select(d=>new{sector_name=d.SectorName,portfolio_weight=d.PortfolioWeight,benchmark_weight=d.BenchmarkWeight,portfolio_return=d.PortfolioReturn,benchmark_return=d.BenchmarkReturn})})).StatusCode);
        Assert.Equal(.3115m,source.GetProperty("portfolio_return").DecimalText());
        Assert.Equal(.127808m,source.GetProperty("benchmark_return").DecimalText());
        Assert.Equal(first.Report.Id,source.GetProperty("periods")[0].GetProperty("report").GetProperty("id").GetString());
        Assert.Equal(2,source.GetProperty("linked_sectors").GetArrayLength());
        Assert.Empty(view.GetProperty("details").EnumerateArray());
        user.Execute("UPDATE attribution_details SET portfolio_return=.9 WHERE report_id=$1 AND sector_name='科技'",first.Report.Id);
        Assert.Equal(HttpStatusCode.Created,(await client.PostAsJsonAsync(Route,body)).StatusCode);
        var reread=await client.GetFromJsonAsync<JsonElement>("/api/v1/attribution/reports/"+id);
        Assert.Equal(source.ToString(),reread.GetProperty("report").GetProperty("source_json").ToString());
        Assert.Equal(3L,user.Query("SELECT count(*) FROM attribution_reports WHERE user_id=$1",user.Id));
        Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync(Route,new{report_id=id,report_ids=new[]{first.Report.Id,Guid.NewGuid().ToString("N")}})).StatusCode);
        foreach(var table in new[]{"transactions","positions","portfolio_events","audit_log"})
            Assert.Equal(0L,user.Query($"SELECT count(*) FROM {table} WHERE user_id=$1",user.Id));
    }

    [PgFact]
    public async Task RejectsForeignReports_DuplicatePeriods_GapsOverlapsAndMalformedEvidence_WithoutSaving()
    {
        using var user=new PgTestUser(); using var other=new PgTestUser();
        var first=Seed(user,new(2026,1,2),new(2026,1,15));
        var second=Seed(user,new(2026,1,15),new(2026,1,30),true);
        var foreign=Seed(other,new(2026,1,15),new(2026,1,30),true);
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",user.Connection);
        using var factory=new WebApplicationFactory<Program>(); using var client=factory.CreateClient();
        PgTestUser.AttachSession(client,user.Connection,user.Id);
        async Task<HttpStatusCode> Link(params string[] ids)=>(await client.PostAsJsonAsync(Route,new{report_id=Guid.NewGuid().ToString("N"),report_ids=ids})).StatusCode;
        Assert.Equal(HttpStatusCode.NotFound,await Link(first.Report.Id,foreign.Report.Id));
        Assert.Equal(HttpStatusCode.UnprocessableEntity,await Link(first.Report.Id,first.Report.Id));
        foreach(var date in new[]{"2026-01-14","2026-01-16","2026-01-30"})
        {
            user.Execute("UPDATE attribution_reports SET start_date=$2::date WHERE id=$1",second.Report.Id,date);
            Assert.Equal(HttpStatusCode.UnprocessableEntity,await Link(first.Report.Id,second.Report.Id));
        }
        user.Execute("UPDATE attribution_reports SET start_date='2026-01-15' WHERE id=$1",second.Report.Id);
        foreach(var (field,value) in new[]{("portfolio_symbol","other"),("benchmark_symbol","other"),("status","draft"),("data_source","native_linked_brinson")})
        {
            user.Execute($"UPDATE attribution_reports SET {field}=$2 WHERE id=$1",second.Report.Id,value);
            Assert.Equal(HttpStatusCode.UnprocessableEntity,await Link(first.Report.Id,second.Report.Id));
            user.Execute($"UPDATE attribution_reports SET {field}=$2 WHERE id=$1",second.Report.Id,field switch{"portfolio_symbol"=>"P","benchmark_symbol"=>"B","status"=>"completed",_=>"manual_sector_input"});
        }
        user.Execute("UPDATE attribution_details SET portfolio_return=NULL WHERE report_id=$1 AND sector_name='科技'",first.Report.Id);
        Assert.Equal(HttpStatusCode.UnprocessableEntity,await Link(first.Report.Id,second.Report.Id));
        user.Execute("UPDATE attribution_details SET portfolio_return=.1 WHERE report_id=$1 AND sector_name='科技'",first.Report.Id);
        user.Execute("UPDATE attribution_reports SET total_active_return=.5 WHERE id=$1",first.Report.Id);
        Assert.Equal(HttpStatusCode.UnprocessableEntity,await Link(first.Report.Id,second.Report.Id));
        Assert.Equal(2L,user.Query("SELECT count(*) FROM attribution_reports WHERE user_id=$1",user.Id));
    }
}
