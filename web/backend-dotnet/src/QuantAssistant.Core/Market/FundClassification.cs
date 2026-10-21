using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace QuantAssistant.Core.Market;

/// <summary>Auditable public-field rules. Fund names and account fee records are never inputs.</summary>
public static class FundClassification
{
    public const string Version="public-fund-rules.2026-10-21.1";
    public static string? Text(JsonNode? node)=>node is JsonValue value&&value.TryGetValue<string>(out var text)?text:null;
    private static string? Asset(string? type)=>type switch
    {
        "股票型" or "股票型-标准指数" or "指数型-股票" or "指数型-海外股票" or "QDII-普通股票"=>"股票型",
        "混合型" or "混合型-偏债" or "混合型-偏股" or "混合型-平衡" or "混合型-灵活" or "混合型-绝对收益"
            or "QDII-混合债" or "QDII-混合偏股" or "QDII-混合平衡" or "QDII-混合灵活"=>"混合型",
        "债券型" or "债券型-中短债" or "债券型-信用债" or "债券型-利率债" or "债券型-混合一级"
            or "债券型-混合二级" or "债券型-长债" or "指数型-固收" or "QDII-纯债"=>"债券型",
        "货币型" or "货币型-普通货币" or "货币型-浮动净值"=>"货币型",
        "FOF-均衡型" or "FOF-稳健型" or "FOF-进取型" or "QDII-FOF"=>"基金组合型",
        "Reits" or "QDII-REITs"=>"不动产型",
        "商品" or "QDII-商品"=>"商品型",
        _=>null
    };
    private static string? Structure(string? type)=>type switch
    {"FOF-均衡型" or "FOF-稳健型" or "FOF-进取型" or "QDII-FOF"=>"FOF","Reits" or "QDII-REITs"=>"REITs",_=>null};

    public static JsonObject Classify(JsonObject entry,JsonObject? detail=null)
    {
        var rawType=Text(entry["fund_type"]);
        var evidence=new JsonArray();var conflicts=new JsonArray();var tags=new HashSet<string>(StringComparer.Ordinal);
        var scopes=new HashSet<string>(StringComparer.Ordinal);var benchmarks=new HashSet<string>(StringComparer.Ordinal);
        var facts=Text(detail?["symbol"])==Text(entry["symbol"])?detail?["classification_facts"] as JsonObject:null;
        if(Text(facts?["schema_version"])!="public-fund-facts.v1" || Text(facts?["source"]) is not ("danjuan" or "sina"))facts=null;
        var fields=facts?["fields"] as JsonObject;
        JsonObject? Field(string key)=>fields?[key] as JsonObject;
        string? Fact(string key)=>Text(Field(key)?["value"]);
        void Proof(string field,string value,string rule,string? fact=null,string status="supported")
        {
            evidence.Add(new JsonObject{["field"]=field,["value"]=value,["rule"]=rule,["status"]=status,
                ["source"]=fact is null?"eastmoney":Text(facts?["source"]),["source_field"]=fact is null?"fund_type":Text(Field(fact)?["source_field"]),
                ["source_text"]=fact is null?rawType:Fact(fact),["source_time"]=null,["fetched_at"]=fact is null?null:Text(facts?["fetched_at"])});
        }
        var asset=Asset(rawType);var profileAsset=Asset(Fact("fund_type"));var structure=Structure(rawType);
        if(asset is not null)Proof("asset_class",asset,"supplier-type-exact");
        if(profileAsset is not null)
        {
            Proof("asset_class",profileAsset,"profile-type-exact","fund_type");
            if(asset is not null&&asset!=profileAsset){asset=null;conflicts.Add("asset_class");}
            else asset=profileAsset;
        }
        if(structure is not null)Proof("product_structure",structure,"supplier-structure-exact");
        if(rawType is "指数型-股票" or "指数型-海外股票" or "指数型-固收" or "指数型-其他")
        {tags.Add("指数策略");Proof("strategy_tags","指数策略","supplier-index-type");}
        if(rawType=="混合型-绝对收益"){tags.Add("绝对收益");Proof("strategy_tags","绝对收益","supplier-absolute-return-type");}
        if(Fact("strategy_type") is "复制指数型" or "指数型" || Fact("fund_type")=="股票型-标准指数")
        {tags.Add("指数策略");Proof("strategy_tags","指数策略","profile-index-type",Fact("strategy_type") is null?"fund_type":"strategy_type");}
        var description=Fact("product_description");
        string? described=description is null?null:
            Regex.IsMatch(description,@"^本基金(?:为|是)(?:一只)?ETF联接基金[，。,；;\s]",RegexOptions.CultureInvariant)?"ETF联接":
            Regex.IsMatch(description,@"^本基金(?:为|是)(?:一只)?交易型开放式指数(?:证券投资)?基金[，。,；;\s]",RegexOptions.CultureInvariant)?"ETF":null;
        if(described is not null)
        {
            Proof("product_structure",described,"explicit-profile-description","product_description");
            if(structure is not null&&structure!=described){structure=null;conflicts.Add("product_structure");}else structure=described;
        }
        // Only an explicit normalized scope list is accepted; narrative exclusions are not guessed.
        var scope=Fact("investment_scope");
        if(scope is not null)
        {
            var parts=Regex.Split(scope.Trim(),@"[、,，/;；]");
            if(parts.Length>0&&parts.All(p=>p.Trim() is "A股" or "港股" or "美股" or "日本" or "全球" or "债券" or "黄金"))
                foreach(var value in parts.Select(p=>p.Trim()).Distinct()){scopes.Add(value);Proof("investment_scope",value,"explicit-scope-list","investment_scope");}
        }
        var benchmark=Fact("benchmark");
        if(benchmark is not null)
            foreach(var index in IndexRegistry.All)
                foreach(var alias in new[]{index.Name,index.OfficialName}.Distinct())
                    if(benchmark.Split(['+','＋']).Any(component=>Regex.IsMatch(component.Trim(),
                        "^"+Regex.Escape(alias)+@"(?:指数)?收益率(?:\s*[×*xX]\s*\d+(?:\.\d+)?\s*%?)?$",RegexOptions.CultureInvariant)))
                        benchmarks.Add(index.Id);
        foreach(var id in benchmarks)Proof("benchmark_index_id",id,"performance-benchmark-formula-component","benchmark");
        var known=asset is not null||structure is not null||tags.Count>0||scopes.Count>0||benchmarks.Count>0;
        return new JsonObject{
            ["symbol"]=Text(entry["symbol"]),["name"]=Text(entry["name"]),["fund_type"]=rawType,
            ["asset_class"]=asset,["product_structure"]=structure,["investment_scope"]=Array(scopes),["strategy_tags"]=Array(tags),
            ["regulatory_category"]=rawType?.StartsWith("QDII-",StringComparison.Ordinal)==true?"QDII":null,
            ["benchmark_index_id"]=benchmarks.Count==1?benchmarks.Single():null,["benchmark_index_ids"]=Array(benchmarks),
            ["benchmark_relation"]=benchmarks.Count>0?"performance_benchmark_component":null,["benchmark_text"]=benchmark,["share_class"]=null,
            ["classification_version"]=Version,["classification_source"]="supplier_fields_and_audited_rules",
            ["classification_status"]=conflicts.Count>0?"conflict":known?"partial":"unknown",
            ["classification_is_stale"]=facts?["is_stale"] is JsonValue flag&&flag.TryGetValue<bool>(out var stale)&&stale,
            ["classification_evidence"]=evidence,["classification_conflicts"]=conflicts,
            ["investment_scope_basis"]="explicit_disclosed_scope_not_current_holdings"
        };
    }
    private static JsonArray Array(IEnumerable<string> values)=>new(values.Order(StringComparer.Ordinal).Select(value=>(JsonNode?)JsonValue.Create(value)).ToArray());
}
