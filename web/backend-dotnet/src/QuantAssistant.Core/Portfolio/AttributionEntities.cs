using System.Text.Json.Serialization;
using System.Text.Json.Nodes;

namespace QuantAssistant.Core.Portfolio;

public class AttributionReport
{
    public string Id { get; set; } = "";
    public string UserId { get; set; } = "";
    public string PortfolioSymbol { get; set; } = "";
    public string BenchmarkSymbol { get; set; } = "";
    public DateOnly StartDate { get; set; }
    public DateOnly EndDate { get; set; }
    
    public decimal? TotalActiveReturn { get; set; }
    public decimal? AllocationEffect { get; set; }
    public decimal? SelectionEffect { get; set; }
    public decimal? InteractionEffect { get; set; }
    
    public string Status { get; set; } = "draft";
    public string DataSource { get; set; } = "manual_sector_input";
    public JsonObject SourceJson { get; set; } = new();
}

public class AttributionDetail
{
    public string ReportId { get; set; } = "";
    public string SectorName { get; set; } = "";
    
    [JsonRequired] public decimal PortfolioWeight { get; set; }
    [JsonRequired] public decimal BenchmarkWeight { get; set; }
    [JsonRequired] public decimal PortfolioReturn { get; set; }
    [JsonRequired] public decimal BenchmarkReturn { get; set; }
    
    public decimal Allocation { get; set; }
    public decimal Selection { get; set; }
    public decimal Interaction { get; set; }
}

public interface IAttributionRepository
{
    void CreateReport(AttributionReport report, IReadOnlyCollection<AttributionDetail>? details = null);
    bool CompleteReport(string userId, AttributionReport report, IReadOnlyCollection<AttributionDetail> details);
    void UpsertDetails(IEnumerable<AttributionDetail> details);
    void UpdateReportStatus(string reportId, string status, decimal activeReturn, decimal allocation, decimal selection, decimal interaction);
    AttributionReport? GetReport(string userId, string reportId);
    AttributionReportView? GetReportView(string userId, string reportId);
    List<AttributionDetail> GetDetails(string reportId);
}

public sealed record AttributionReportView(AttributionReport Report, List<AttributionDetail> Details);
public sealed record BrinsonHolding(string Symbol, string SectorName, decimal InitialValue, decimal PeriodReturn);
