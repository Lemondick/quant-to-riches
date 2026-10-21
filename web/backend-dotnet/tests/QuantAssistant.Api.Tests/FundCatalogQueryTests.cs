using System.Text.Json.Nodes;
using QuantAssistant.Api.Services;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class FundCatalogQueryTests
{
    private static JsonObject Row(string code,string asset,string[] tags)=>new(){["symbol"]=code,["name"]="明确测试基金"+code,["asset_class"]=asset,["strategy_tags"]=new JsonArray(tags.Select(x=>(JsonNode?)JsonValue.Create(x)).ToArray()),["investment_scope"]=new JsonArray(),["share_class"]=null};
    [Fact]
    public void FiltersCombineWithSearchAndStablePaginationWithoutMutatingCache()
    {
        JsonObject[] rows=[Row("000003","股票型",["指数策略"]),Row("000001","股票型",["指数策略","绝对收益"]),Row("000002","债券型",[])];
        var query=new FundCatalogFilter("000",1,1,new(){["asset_class"]=["股票型"], ["strategy_tags"]=["指数策略"]});
        var result=FundCatalogQuery.Apply(rows,query);
        Assert.Equal(2,result["total"]!.GetValue<int>());Assert.Equal("000001",result["items"]![0]!["symbol"]!.GetValue<string>());
        result["items"]![0]!["name"]="mutated";Assert.NotEqual("mutated",rows[1]["name"]!.GetValue<string>());
        var allTags=FundCatalogQuery.Apply(rows,query with{Filters=new(){["strategy_tags"]=["指数策略","绝对收益"]}});
        Assert.Equal(1,allTags["total"]!.GetValue<int>());
        Assert.Equal(3,FundCatalogQuery.Apply(rows,query with{Filters=new(){["share_class"]=["__unknown"]},PageSize=50})["total"]!.GetValue<int>());
    }
    [Fact]
    public void RequestBoundsAndUnknownDimensionsAreRejected()
    {
        foreach(var query in new[]{new FundCatalogFilter("",0,50,new()),new FundCatalogFilter("",1,101,new()),new FundCatalogFilter(new string('a',101),1,50,new()),new FundCatalogFilter("",1,50,new(){["name"]=["ETF"]})})
            Assert.Throws<ArgumentException>(()=>FundCatalogQuery.Apply([],query));
    }
}
