using System.Text.Json.Nodes;
using QuantAssistant.Core.Market;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class FundClassificationTests
{
    private static JsonObject Entry(string type,string name="名称故意含黄金红利ETF联接C")=>new(){["symbol"]="023530",["name"]=name,["fund_type"]=type};
    private static JsonObject Facts(params (string Key,string Value)[] fields)=>new(){["symbol"]="023530",["classification_facts"]=new JsonObject{
        ["schema_version"]="public-fund-facts.v1",["source"]="danjuan",["source_time"]=null,["fetched_at"]="2026-10-20T16:00:00Z",
        ["fields"]=new JsonObject(fields.Select(f=>KeyValuePair.Create<string,JsonNode?>(f.Key,new JsonObject{["value"]=f.Value,["source_field"]=f.Key})))}};

    [Fact]
    public void NameAndComparisonListsNeverCreateClassifications()
    {
        var detail=Facts(); detail["benchmark_index"]=new JsonArray("沪深300");
        var result=FundClassification.Classify(Entry("未知供应商类型"),detail);
        Assert.Null(result["asset_class"]);Assert.Null(result["product_structure"]);Assert.Null(result["share_class"]);
        Assert.Empty(result["strategy_tags"]!.AsArray());Assert.Null(result["benchmark_index_id"]);
        Assert.Equal("unknown",result["classification_status"]!.GetValue<string>());
    }
    [Theory]
    [InlineData("股票型","股票型",null)]
    [InlineData("指数型-股票","股票型",null)]
    [InlineData("QDII-普通股票","股票型",null)]
    [InlineData("QDII-REITs","不动产型","REITs")]
    [InlineData("FOF-稳健型","基金组合型","FOF")]
    [InlineData("货币型-普通货币","货币型",null)]
    public void ProviderTypeHasIndependentAssetAndStructure(string raw,string asset,string? structure)
    {
        var result=FundClassification.Classify(Entry(raw));
        Assert.Equal(asset,result["asset_class"]!.GetValue<string>());
        Assert.Equal(structure,result["product_structure"]?.GetValue<string>());
        Assert.Null(result["share_class"]);
    }
    [Fact]
    public void ActualProfileFactsSupportMultipleTagsButAviationBenchmarkDoesNotBecomeCsi300()
    {
        var detail=Facts(("product_description","本基金为ETF联接基金，主要通过投资于目标ETF实现跟踪"),("strategy_type","复制指数型"),
            ("benchmark","国证通用航空产业指数收益率×95%+银行活期存款利率（税后）×5%"));
        var result=FundClassification.Classify(Entry("指数型-股票"),detail);
        Assert.Equal("ETF联接",result["product_structure"]!.GetValue<string>());
        Assert.Contains("指数策略",result["strategy_tags"]!.AsArray().Select(x=>x!.GetValue<string>()));
        Assert.Null(result["benchmark_index_id"]);
        Assert.NotEmpty(result["classification_evidence"]!.AsArray());
    }
    [Theory]
    [InlineData("主动型","股票型-标准指数","fund_type","股票型-标准指数")]
    [InlineData("","股票型-标准指数","fund_type","股票型-标准指数")]
    [InlineData("复制指数型","股票型","strategy_type","复制指数型")]
    [InlineData("指数型","股票型-标准指数","strategy_type","指数型")]
    public void IndexStrategyEvidenceNamesTheFieldThatActuallyMatched(string strategy,string type,string sourceField,string sourceText)
    {
        var result=FundClassification.Classify(Entry("股票型"),Facts(("strategy_type",strategy),("fund_type",type)));
        Assert.Contains("指数策略",result["strategy_tags"]!.AsArray().Select(item=>item!.GetValue<string>()));
        var proof=Assert.Single(result["classification_evidence"]!.AsArray().OfType<JsonObject>(),item=>item["rule"]!.GetValue<string>()=="profile-index-type");
        Assert.Equal(sourceField,proof["source_field"]!.GetValue<string>());
        Assert.Equal(sourceText,proof["source_text"]!.GetValue<string>());
    }
    [Fact]
    public void FormulaAssociationIsExplicitlyABenchmarkComponentNotAnAssumedTrackingRelationship()
    {
        var result=FundClassification.Classify(Entry("指数型-股票"),Facts(("benchmark","沪深300指数收益率×95%+活期存款利率(税后)×5%")));
        Assert.Equal("cn.csi.300",result["benchmark_index_id"]!.GetValue<string>());
        Assert.Equal("performance_benchmark_component",result["benchmark_relation"]!.GetValue<string>());
        Assert.Null(result["share_class"]);
        foreach(var falseMatch in new[]{"非沪深300指数收益率","沪深3000指数收益率","本基金不以沪深300指数为基准"})
            Assert.Null(FundClassification.Classify(Entry("股票型"),Facts(("benchmark",falseMatch)))["benchmark_index_id"]);
    }
    [Fact]
    public void ConflictingAssetFactsAreUnknownAndNeverBorrowAnotherFundProfile()
    {
        var result=FundClassification.Classify(Entry("债券型-长债"),Facts(("fund_type","股票型")));
        Assert.Null(result["asset_class"]); Assert.Equal("conflict",result["classification_status"]!.GetValue<string>());
        var other=Facts(("product_description","本基金为ETF联接基金"));other["symbol"]="110020";
        Assert.Null(FundClassification.Classify(Entry("股票型"),other)["product_structure"]);
    }
    [Fact]
    public void MalformedOptionalProfileFieldsDoNotHideAQualifiedDirectoryEntry()
    {
        var detail=Facts();detail["classification_facts"]!["fields"]!["benchmark"]="wrong-shape";
        var result=FundClassification.Classify(Entry("股票型"),detail);
        Assert.Equal("股票型",result["asset_class"]!.GetValue<string>());Assert.Null(result["benchmark_index_id"]);
        var ambiguous=FundClassification.Classify(Entry("QDII-混合债"));Assert.Null(ambiguous["asset_class"]);Assert.Equal("QDII",ambiguous["regulatory_category"]!.GetValue<string>());
    }
}
