using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Market;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Services;

/// <summary>One bounded public directory; no user preferences, per-row crawlers or financial writes.</summary>
public sealed class FundCatalogService(PublicSnapshotRepository repository,ComputeService compute,TimeProvider clock,TradingCalendarRepository calendars)
{
    private sealed record Snapshot(JsonObject Metadata,JsonObject[] Rows,JsonObject Categories,DateTimeOffset LoadedAt)
    {
        public int RefreshRequested;
    }
    private readonly object gate=new();
    private Snapshot? saved;
    private Lazy<Task<Snapshot>>? pending;
    private async Task<Snapshot> Load()
    {
        try
        {
            using var deadline=new CancellationTokenSource(TimeSpan.FromSeconds(10));
            var source=await repository.ReadAsync("fund.catalog","{}",deadline.Token)
                ??throw new ApplicationFailure("public_snapshot_unavailable",503,"基金目录正在准备，请稍后刷新。");
            var data=JsonNode.Parse(source.ResultJson)!.AsObject();
            if(FundClassification.Text(data["schema_version"])!="fund-directory.v1"||FundClassification.Text(data["source"])!="eastmoney"
                ||FundClassification.Text(data["quality_status"]) is not ("valid" or "unknown_time" or "stale")
                ||data["items"] is not JsonArray {Count:>0 and <=50000} items
                ||!DateTimeOffset.TryParse(FundClassification.Text(data["fetched_at"]),CultureInfo.InvariantCulture,DateTimeStyles.None,out var fetched)
                ||fetched>clock.GetUtcNow().AddMinutes(1)||fetched<clock.GetUtcNow().AddDays(-7))
                throw new ApplicationFailure("public_snapshot_unavailable",503,"基金目录未通过校验。");
            var facts=await repository.FundClassificationFactsAsync(deadline.Token);
            var codes=new HashSet<string>(StringComparer.Ordinal);
            var rows=new List<JsonObject>(items.Count);
            foreach(var node in items)
            {
                if(node is not JsonObject entry||FundClassification.Text(entry["symbol"]) is not {Length:6} code||!code.All(char.IsAsciiDigit)
                    ||FundClassification.Text(entry["name"]) is not {Length:>0 and <=200}||!codes.Add(code)
                    ||entry.Any(pair=>pair.Key is not ("symbol" or "name" or "fund_type")))
                    throw new ApplicationFailure("public_snapshot_unavailable",503,"基金目录存在身份冲突。");
                var profile=facts.GetValueOrDefault(code);
                var classified=FundClassification.Classify(entry,profile);
                if(DateTimeOffset.TryParse(FundClassification.Text(profile?["classification_facts"]?["fetched_at"]),CultureInfo.InvariantCulture,DateTimeStyles.None,out var profileTime)
                    &&clock.GetUtcNow()-profileTime>TimeSpan.FromDays(1))classified["classification_is_stale"]=true;
                rows.Add(classified);
            }
            var stale=!source.Fresh||clock.GetUtcNow()-fetched>TimeSpan.FromHours(6)||data["stale"]?.GetValue<bool>()==true;
            var metadata=new JsonObject{["data_source"]="eastmoney",["source_time"]=null,["fetched_at"]=fetched.ToString("O"),
                ["quality_status"]=stale?"stale":"unknown_time",["stale"]=stale,["classification_version"]=FundClassification.Version,
                ["directory_count"]=rows.Count,["coverage_status"]="supplier_directory_only",["source_url"]="https://fund.eastmoney.com/js/fundcode_search.js",
                ["rejected_count"]=data["rejected_count"]?.DeepClone(),["classification_note"]="仅按供应商字段和公开资料分类；未知不按名称猜测，业绩基准关联不代表实际跟踪。"};
            var result=new Snapshot(metadata,rows.ToArray(),FundCatalogQuery.Categories(rows.ToArray()),clock.GetUtcNow());
            lock(gate)saved=result;
            return result;
        }
        finally {lock(gate)pending=null;}
    }
    private async Task<Snapshot> Read(CancellationToken cancellation)
    {
        Lazy<Task<Snapshot>> task;
        lock(gate)
        {
            if(saved is { } current&&clock.GetUtcNow()-current.LoadedAt<TimeSpan.FromSeconds(60))return current;
            task=pending??=new(()=>Load());
        }
        return await task.Value.WaitAsync(cancellation);
    }
    public async Task<JsonObject> ReadAsync(string userId,FundCatalogFilter filter,bool categories,CancellationToken cancellation)
    {
        FundCatalogQuery.Validate(filter);
        Snapshot source;
        try{source=await Read(cancellation);}
        catch(ApplicationFailure){await compute.QueuePublicRefresh(userId,"fund.catalog",new{},cancellation,force:true);throw;}
        // A completed refresh must not be queued again by readers of the same retained stale snapshot.
        if(source.Metadata["stale"]?.GetValue<bool>()==true&&Interlocked.CompareExchange(ref source.RefreshRequested,1,0)==0)
        {
            try{await compute.QueuePublicRefresh(userId,"fund.catalog",new{},cancellation,force:true);}
            catch{Interlocked.Exchange(ref source.RefreshRequested,0);throw;}
        }
        var result=categories?new JsonObject{["dimensions"]=source.Categories.DeepClone()}:FundCatalogQuery.Apply(source.Rows,filter);
        foreach(var pair in source.Metadata)result[pair.Key]=pair.Value?.DeepClone();
        result["cache_read_at"]=clock.GetUtcNow().ToString("O");
        if(!categories)
        {
            var rows=result["items"]!.AsArray().OfType<JsonObject>().ToArray();
            foreach(var row in rows)
            {
                row["data_source"]=source.Metadata["data_source"]?.DeepClone();row["source_time"]=null;
                row["fetched_at"]=source.Metadata["fetched_at"]?.DeepClone();row["quality_status"]=source.Metadata["quality_status"]?.DeepClone();
                foreach(var proof in row["classification_evidence"]!.AsArray().OfType<JsonObject>().Where(proof=>FundClassification.Text(proof["source"])=="eastmoney"&&FundClassification.Text(proof["source_field"])=="fund_type"))
                    proof["fetched_at"]=source.Metadata["fetched_at"]?.DeepClone();
            }
            if(rows.Length>0)
            {
                var navs=await repository.OfficialNavAsync(rows.Select(row=>row["symbol"]!.GetValue<string>()).ToArray(),cancellation);
                foreach(var row in rows)ApplyNav(row,navs[row["symbol"]!.GetValue<string>()] as JsonObject,clock.GetUtcNow(),calendars.CurrentSnapshot);
            }
        }
        return result;
    }
    public static void ApplyNav(JsonObject row,JsonObject? detail,DateTimeOffset now,MarketCalendars calendars)
    {
        row["unit_nav"]=null;row["nav_date"]=null;row["daily_return"]=null;row["nav_source"]=null;row["nav_fetched_at"]=null;
        row["nav_quality_status"]="unavailable";row["nav_reason"]="official_nav_not_cached";
        if(FundClassification.Text(row["asset_class"])=="货币型"||new[]{FundClassification.Text(row["fund_type"]),FundClassification.Text(detail?["fund_type"])}.Any(type=>type?.StartsWith("货币型",StringComparison.Ordinal)==true)) {row["nav_reason"]="money_fund_metric_not_verified";return;}
        var point=(detail?["points"] as JsonArray)?.OfType<JsonObject>().OrderBy(item=>FundClassification.Text(item["nav_date"]),StringComparer.Ordinal).LastOrDefault();
        if(point is null||!DateOnly.TryParseExact(FundClassification.Text(point["nav_date"]),"yyyy-MM-dd",CultureInfo.InvariantCulture,DateTimeStyles.None,out var day)
            ||day>DateOnly.FromDateTime(now.ToOffset(TimeSpan.FromHours(8)).DateTime)
            ||!decimal.TryParse(FundClassification.Text(point["unit_nav"]),NumberStyles.AllowDecimalPoint,CultureInfo.InvariantCulture,out var nav)||nav<=0
            ||FundClassification.Text(point["source"]) is not ("eastmoney" or "tencent" or "danjuan"))return;
        var kind=FundClassification.Text(detail?["fund_type"])??FundClassification.Text(row["fund_type"]);
        var policy=FundClassification.Text(detail?["nav_freshness_policy"])??NavFreshness.PolicyForFund(kind?.StartsWith("QDII",StringComparison.Ordinal)==true?kind:FundClassification.Text(row["asset_class"]));
        int? Integer(string key)=>detail?[key] is JsonValue value&&value.TryGetValue<int>(out var number)?number:null;
        var freshness=NavFreshness.Evaluate(day,now,calendars,policy,Integer("nav_valuation_weekday"),publicationLag:Integer("nav_publication_lag_sessions"));
        row["nav_freshness_policy"]=policy;
        row["unit_nav"]=point["unit_nav"]!.DeepClone();row["nav_date"]=day.ToString("yyyy-MM-dd");row["nav_source"]=point["source"]!.DeepClone();
        row["nav_fetched_at"]=detail?["fetched_at"]?.DeepClone();row["nav_quality_status"]=freshness.Status=="fresh"?"valid":freshness.Status=="stale"?"stale":"unknown_time";row["nav_reason"]=freshness.Reason;
        var returnErrors=point["validation_errors"] as JsonArray;
        if((returnErrors is null||returnErrors.Count==0)&&decimal.TryParse(FundClassification.Text(point["daily_return"]),NumberStyles.AllowLeadingSign|NumberStyles.AllowDecimalPoint,CultureInfo.InvariantCulture,out _))row["daily_return"]=point["daily_return"]!.DeepClone();
    }
}
