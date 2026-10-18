using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class AttributionCompletionTests
{
    private static object Details(string name = "行业", decimal weight = 1m, decimal portfolioReturn = 0.1m) => new[]
    {
        new { sector_name = name, portfolio_weight = weight, benchmark_weight = 1m, portfolio_return = portfolioReturn, benchmark_return = 0.04m }
    };
    private static object Body(object details, string start = "2026-01-01", string end = "2026-01-31") => new
    { portfolio_symbol = "P1", benchmark_symbol = "000300.SH", start_date = start, end_date = end, details };

    [PgFact]
    public async Task CompleteCreate_RecalculateReplacesDetails_AndRejectsInvalidOrForeignWrites()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        using var otherClient = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        PgTestUser.AttachSession(otherClient, user.Connection, other.Id);
        var created = await client.PostAsJsonAsync("/api/v1/attribution/reports", Body(Details("旧行业")));
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);
        var report = await created.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("completed", report.GetProperty("status").GetString());
        Assert.Equal(0.06m, report.GetProperty("total_active_return").DecimalText());
        var id = report.GetProperty("id").GetString();
        var path = $"/api/v1/attribution/reports/{id}";
        Assert.Equal(HttpStatusCode.OK, (await client.PostAsJsonAsync(path + "/calculate", new { details = Details("新行业", portfolioReturn: -0.1m) })).StatusCode);
        var saved = await client.GetFromJsonAsync<JsonElement>(path);
        var rows = saved.GetProperty("details");
        Assert.Equal("新行业", Assert.Single(rows.EnumerateArray()).GetProperty("sector_name").GetString());
        var totals = saved.GetProperty("report");
        Assert.Equal(-0.14m, totals.GetProperty("total_active_return").DecimalText());
        Assert.Equal(totals.GetProperty("total_active_return").DecimalText(), totals.GetProperty("allocation_effect").DecimalText() + totals.GetProperty("selection_effect").DecimalText() + totals.GetProperty("interaction_effect").DecimalText());
        Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync(path + "/calculate", new { details = Details(weight: 0.5m) })).StatusCode);
        var after = await client.GetFromJsonAsync<JsonElement>(path);
        Assert.Equal(saved.ToString(), after.ToString());
        Assert.Equal(HttpStatusCode.NotFound, (await otherClient.GetAsync(path)).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await otherClient.PostAsJsonAsync(path + "/calculate", new { details = Details() })).StatusCode);
    }

    [PgFact]
    public async Task InvalidInput_DoesNotCreateEmptyReports()
    {
        using var user = new PgTestUser();
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        foreach (var body in new[]
        {
            Body(Array.Empty<object>()), Body(Details(weight: -1m)), Body(Details(weight: 0.5m)),
            Body(Details(portfolioReturn: -1.1m)), Body(Details(" ")), Body(Details(), "2026-02-01", "2026-01-01"),
            Body(Details(), "01/01/2026"), Body(new[]
            {
                new { sector_name = "重复", portfolio_weight = 0.5m, benchmark_weight = 0.5m, portfolio_return = 0m, benchmark_return = 0m },
                new { sector_name = " 重复 ", portfolio_weight = 0.5m, benchmark_weight = 0.5m, portfolio_return = 0m, benchmark_return = 0m }
            })
        })
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/api/v1/attribution/reports", body)).StatusCode);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync("/api/v1/attribution/reports", Body(new[] { new { sector_name = "缺收益", portfolio_weight = 1m, benchmark_weight = 1m } }))).StatusCode);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM attribution_reports WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public void StorageFailure_RollsBackDeletedDetailsAndSummary()
    {
        using var user = new PgTestUser();
        var repository = new AttributionRepository(user.Connection);
        var report = new AttributionReport { Id = Guid.NewGuid().ToString("N"), UserId = user.Id, PortfolioSymbol = "P1", BenchmarkSymbol = "000300.SH", StartDate = new(2026, 1, 1), EndDate = new(2026, 1, 31), Status = "completed", TotalActiveReturn = 0.06m, AllocationEffect = 0m, SelectionEffect = 0.06m, InteractionEffect = 0m };
        repository.CreateReport(report, new[] { new AttributionDetail { ReportId = report.Id, SectorName = "原行业", PortfolioWeight = 1m, BenchmarkWeight = 1m, PortfolioReturn = 0.1m, BenchmarkReturn = 0.04m, Selection = 0.06m } });
        report.TotalActiveReturn = 0m;
        var invalid = new[] { new AttributionDetail { ReportId = report.Id, SectorName = "重复" }, new AttributionDetail { ReportId = report.Id, SectorName = "重复" } };
        var error = Assert.Throws<PostgresException>(() => repository.CompleteReport(user.Id, report, invalid));
        Assert.Equal(PostgresErrorCodes.UniqueViolation, error.SqlState);
        Assert.Equal(0.06m, repository.GetReport(user.Id, report.Id)!.TotalActiveReturn);
        Assert.Equal("原行业", Assert.Single(repository.GetDetails(report.Id)).SectorName);
    }
}
