using System.Text.Json.Nodes;
using QuantAssistant.Core.Market;

namespace QuantAssistant.Api.Services;

public sealed record FundCatalogFilter(string Query,int Page,int PageSize,Dictionary<string,string[]> Filters);
public static class FundCatalogQuery
{
    public static readonly string[] Dimensions=["fund_type","asset_class","investment_scope","strategy_tags","product_structure","benchmark_index_id","share_class","regulatory_category","classification_status"];
    public static string[] Values(JsonObject row,string key)
    {
        var node=key=="benchmark_index_id"?row["benchmark_index_ids"]:row[key];
        var values=node is JsonArray array?array.Select(FundClassification.Text).OfType<string>().ToArray():FundClassification.Text(node) is { } text?[text]:System.Array.Empty<string>();
        return values.Length==0?["__unknown"]:values;
    }
    public static void Validate(FundCatalogFilter query)
    {
        if(query.Page is <1 or >1000000||query.PageSize is <1 or >100||query.Query.Length>100
            ||query.Filters.Any(pair=>!Dimensions.Contains(pair.Key)||pair.Value.Length>20||pair.Value.Any(value=>string.IsNullOrWhiteSpace(value)||value.Length>100)))
            throw new ArgumentException("基金目录筛选或分页参数无效");
    }
    public static JsonObject Apply(JsonObject[] rows,FundCatalogFilter query)
    {
        Validate(query);
        var text=query.Query.Trim();
        var selected=rows.Where(row=>(text.Length==0||(FundClassification.Text(row["symbol"])??"").Contains(text,StringComparison.OrdinalIgnoreCase)
            ||(FundClassification.Text(row["name"])??"").Contains(text,StringComparison.OrdinalIgnoreCase))
            &&query.Filters.All(pair=>pair.Value.Length==0||(pair.Key=="strategy_tags"?pair.Value.All(Values(row,pair.Key).Contains):pair.Value.Any(Values(row,pair.Key).Contains))))
            .OrderBy(row=>FundClassification.Text(row["symbol"]),StringComparer.Ordinal).ToArray();
        return new(){["total"]=selected.Length,["page"]=query.Page,["page_size"]=query.PageSize,
            ["items"]=new JsonArray(selected.Skip((query.Page-1)*query.PageSize).Take(query.PageSize).Select(row=>row.DeepClone()).ToArray())};
    }
    public static JsonObject Categories(JsonObject[] rows)=>new(Dimensions.Select(key=>KeyValuePair.Create<string,JsonNode?>(key,
        new JsonArray(rows.SelectMany(row=>Values(row,key).Distinct()).GroupBy(value=>value).OrderBy(group=>group.Key,StringComparer.Ordinal)
            .Select(group=>(JsonNode?)new JsonObject{["value"]=group.Key,["label"]=group.Key=="__unknown"?"未分类 / 待核验":group.Key,["count"]=group.Count()}).ToArray()))));
}
