using System.Text.Json.Serialization;

namespace QuantAssistant.Core.Portfolio;

public class PeerGroup
{
    public string Id { get; set; } = "";
    public string GroupName { get; set; } = "";
    public string BenchmarkSymbol { get; set; } = "";
}

public class PeerRanking
{
    public string Symbol { get; set; } = "";
    public string GroupId { get; set; } = "";
    public DateOnly DataDate { get; set; }
    
    [JsonPropertyName("return_1m")]
    public decimal? Return1M { get; set; }
    
    [JsonPropertyName("rank_1m")]
    public int? Rank1M { get; set; }
    
    [JsonPropertyName("percentile_1m")]
    public decimal? Percentile1M { get; set; }
    
    [JsonPropertyName("return_1y")]
    public decimal? Return1Y { get; set; }
    
    [JsonPropertyName("rank_1y")]
    public int? Rank1Y { get; set; }
    
    [JsonPropertyName("percentile_1y")]
    public decimal? Percentile1Y { get; set; }
    
    [JsonPropertyName("excess_return_1y")]
    public decimal? ExcessReturn1Y { get; set; }
}

public interface IPeerRepository
{
    void CreateGroup(PeerGroup group);
    void AssignFundToGroup(string symbol, string groupId);
    void UpsertRankings(IEnumerable<PeerRanking> rankings);
    List<PeerRanking> GetRankingsByGroup(string groupId, DateOnly date);
    PeerRanking? GetFundRanking(string symbol, string groupId, DateOnly date);
}

public static class PeerRules
{
    /// <summary>
    /// 给定一组同类基金的收益率，计算绝对排名与百分位得分
    /// 百分位 = (总数 - 排名) / (总数 - 1) * 100
    /// </summary>
    public static void ComputeRankings(List<PeerRanking> groupRankings)
    {
        if (groupRankings.Count == 0) return;
        if (groupRankings.Count == 1)
        {
            var single = groupRankings[0];
            single.Rank1M = single.Return1M.HasValue ? 1 : null;
            single.Percentile1M = single.Return1M.HasValue ? 100m : null;
            single.Rank1Y = single.Return1Y.HasValue ? 1 : null;
            single.Percentile1Y = single.Return1Y.HasValue ? 100m : null;
            return;
        }

        // 1M
        var valid1M = groupRankings.Where(r => r.Return1M.HasValue).OrderByDescending(r => r.Return1M).ToList();
        for (int i = 0; i < valid1M.Count; i++)
        {
            valid1M[i].Rank1M = i + 1;
            // 百分位: 排名第1(i=0)就是 100%, 最后一名就是 0%
            if (valid1M.Count > 1)
                valid1M[i].Percentile1M = Math.Round((decimal)(valid1M.Count - 1 - i) / (valid1M.Count - 1) * 100m, 2);
            else
                valid1M[i].Percentile1M = 100m;
        }

        // 1Y
        var valid1Y = groupRankings.Where(r => r.Return1Y.HasValue).OrderByDescending(r => r.Return1Y).ToList();
        for (int i = 0; i < valid1Y.Count; i++)
        {
            valid1Y[i].Rank1Y = i + 1;
            if (valid1Y.Count > 1)
                valid1Y[i].Percentile1Y = Math.Round((decimal)(valid1Y.Count - 1 - i) / (valid1Y.Count - 1) * 100m, 2);
            else
                valid1Y[i].Percentile1Y = 100m;
        }
    }
}
