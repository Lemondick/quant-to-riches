namespace QuantAssistant.Core.Portfolio;

public class DcaPlan
{
    public string Id { get; set; } = "";
    public string UserId { get; set; } = "";
    public string Symbol { get; set; } = "";
    public string BenchmarkSymbol { get; set; } = "";
    public decimal BaseAmount { get; set; }
    
    public int MaDays { get; set; } = 250;
    public decimal MinMultiplier { get; set; } = 0.5m;
    public decimal MaxMultiplier { get; set; } = 2.0m;
    
    public string Status { get; set; } = "active";
}

public interface IDcaRepository
{
    void CreatePlan(DcaPlan plan);
    List<DcaPlan> ListActivePlans(string userId);
    void UpdatePlanStatus(string userId, string planId, string status);
}
