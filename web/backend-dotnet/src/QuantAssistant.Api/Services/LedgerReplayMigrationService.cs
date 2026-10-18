using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;
using Action=QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Api.Services;

public sealed class LedgerReplayMigrationService(AccountDatabase database,ComputeService compute)
{
    public sealed record HistoricalBody(string Symbol,string AssetType,string Action,decimal Quantity,decimal Price,decimal Fees,DateTimeOffset OccurredAt,string ExternalId,string Reason,string? CashSymbol=null,string? Note=null);
    private static ApplicationFailure Conflict(string message)=>new("ledger_review_conflict",409,message);
    private static decimal Amount(JsonNode row,string key)=>decimal.Parse(row[key]!.GetValue<string>(),CultureInfo.InvariantCulture);
    private static JsonObject State(AccountSql sql,string user)=>new(){["financial"]=FinancialSnapshot.Read(sql,user),["sources"]=sql.Query("SELECT id,sha256,status,path,row_count,issues_json FROM data_snapshots WHERE user_id=$1 ORDER BY id",user)};
    private static TransactionInput Input(HistoricalBody body)
    {
        var type=body.AssetType switch{"stock"=>Core.Portfolio.AssetType.Stock,"fund"=>Core.Portfolio.AssetType.Fund,_=>throw new ArgumentException("资产类型无效")};
        var action=body.Action switch{"buy"=>Action.Buy,"add"=>Action.Add,"sell"=>Action.Sell,"reduce"=>Action.Reduce,_=>throw new ArgumentException("成交动作无效")};
        if(string.IsNullOrWhiteSpace(body.Reason)||body.Reason.Length>1000||body.ExternalId.Length is <1 or >128||body.Symbol.Length is <1 or >32||body.Note?.Length>500||body.CashSymbol?.Length>32||body.OccurredAt>DateTimeOffset.UtcNow)throw new ArgumentException("补录标识、时间或原因无效");
        return new(Symbols.Canonical(body.Symbol,type),type,action,body.Quantity,body.Price,body.Fees,body.OccurredAt,body.ExternalId,"historical-manual",body.CashSymbol,body.Note);
    }
    private static JsonObject Plan(TransactionInput item)=>new(){["symbol"]=item.Symbol,["asset_type"]=item.AssetType.ToString().ToLowerInvariant(),["action"]=item.Action.ToString().ToLowerInvariant(),["quantity"]=item.Quantity.ToString(CultureInfo.InvariantCulture),["price"]=item.Price.ToString(CultureInfo.InvariantCulture),["fees"]=item.Fees.ToString(CultureInfo.InvariantCulture),["traded_at"]=item.TradedAt.ToString("O"),["external_id"]=item.ExternalId,["cash_symbol"]=item.CashSymbol,["note"]=item.Note,["source"]="historical-manual"};
    public async Task<JsonObject> Preview(string user,HistoricalBody body,CancellationToken cancellation)
    {
        var item=Input(body);TransactionRules.Validate(item);
        var input=database.Read(sql=>
        {
            if(sql.Scalar("SELECT 1 FROM transactions WHERE user_id=$1 AND source='historical-manual' AND external_id=$2",user,item.ExternalId) is not null)throw Conflict("这个补录编号已存在，请查看历史记录。");
            return new JsonObject{["state"]=State(sql,user),["snapshot"]=AnalysisSnapshotRepository.Create(sql,user)};
        });
        var result=(await compute.Run(user,"ledger.replay",new{snapshot=input["snapshot"],transaction=Plan(item)},cancellation,seconds:180))!.AsObject();
        if(result["status"]!.GetValue<string>()!="ready"){result["preview_id"]=null;return result;}
        ValidateResult(result);
        var id=Guid.NewGuid().ToString("N");var payload=new JsonObject{["status"]="ready",["preview_id"]=id,["expires_at"]=DateTimeOffset.UtcNow.AddMinutes(15).ToOffset(TimeSpan.FromHours(8)).ToString("O"),["positions"]=result["positions"]!.DeepClone(),["valuations"]=result["valuations"]!.DeepClone(),["before_positions"]=input["state"]!["financial"]!["positions"]!.DeepClone(),["before_valuations"]=input["state"]!["financial"]!["portfolio_valuations"]!.DeepClone(),["blockers"]=new JsonArray(),["reason"]=body.Reason,["limitations"]=new JsonArray("原始成交和报告保留；回算会追加新成交及净值修订审计。","持仓变化后需重新核对基金批次和费率；历史回算不计入前向模拟天数。")};
        var plan=new JsonObject{["transaction"]=Plan(item),["reason"]=body.Reason};
        database.Write(user,sql=>
        {
            if(FinancialSnapshot.Hash(State(sql,user))!=FinancialSnapshot.Hash(input["state"]!))throw Conflict("账本或估值证据已变化，请重新预览。");
            sql.Execute("INSERT INTO ledger_replay_previews(user_id,preview_id,source_fingerprint,plan_json,result_json,created_at,expires_at) VALUES($1,$2,$3,$4::json,$5::json,now(),now()+interval '15 minutes')",user,id,FinancialSnapshot.Hash(input["state"]!),plan.ToJsonString(),result.ToJsonString());return 0;
        });return payload;
    }
    public async Task<JsonObject> BatchPreview(string user,LedgerCsvInput.Batch body,CancellationToken cancellation)
    {
        var bodies=LedgerCsvInput.Validate(body);var items=bodies.Select(value=>{var item=Input(value);TransactionRules.Validate(item);return item;}).ToArray();
        var rows=new JsonArray();var fresh=new List<int>();
        var input=database.Read(sql=>
        {
            var state=State(sql,user);var financial=state["financial"]!.AsObject();
            var cash=financial["positions"]!.AsArray().Where(p=>p!["asset_type"]!.GetValue<string>()=="cash").ToArray();
            if(cash.Length!=1)throw new ApplicationFailure("invalid_request",422,"批量导入需要唯一现金账户及已确认的完整账户基线。");
            var cashSymbol=cash[0]!["symbol"]!.GetValue<string>();
            var instruments=financial["instruments"]!.AsArray().OfType<JsonObject>().ToDictionary(p=>p["symbol"]!.GetValue<string>());
            var existing=financial["transactions"]!.AsArray().OfType<JsonObject>().Where(p=>p["external_id"] is not null).ToLookup(p=>p["external_id"]!.GetValue<string>());
            for(var index=0;index<items.Length;index++)
            {
                var item=items[index];var label=body.Trades[index].Row>0?$"CSV 第 {body.Trades[index].Row} 行":$"第 {index+1} 笔";
                if(!instruments.TryGetValue(item.Symbol,out var instrument)||instrument["asset_type"]!.GetValue<string>()!=bodies[index].AssetType)throw new ApplicationFailure("invalid_request",422,$"{label}：代码尚未登记或资产类型不一致，请先核对标的。");
                if(item.CashSymbol is not null && item.CashSymbol!=cashSymbol)throw new ApplicationFailure("invalid_request",422,$"{label}：现金代码与账户不一致。");
                var matches=existing[item.ExternalId].ToArray();
                var duplicate=matches.Length==1 && matches[0]["symbol"]!.GetValue<string>()==item.Symbol && matches[0]["asset_type"]!.GetValue<string>()==bodies[index].AssetType && matches[0]["action"]!.GetValue<string>()==bodies[index].Action && Amount(matches[0],"quantity")==item.Quantity && Amount(matches[0],"price")==item.Price && Amount(matches[0],"fees")==item.Fees && DateTimeOffset.Parse(matches[0]["traded_at"]!.GetValue<string>(),CultureInfo.InvariantCulture)==item.TradedAt && matches[0]["cash_symbol"]!.GetValue<string>()==cashSymbol;
                var status=matches.Length==0?"new":duplicate?"duplicate":"conflict";
                rows.Add(new JsonObject{["row"]=body.Trades[index].Row>0?body.Trades[index].Row:index+2,["external_id"]=item.ExternalId,["status"]=status,["transaction_id"]=duplicate?matches[0]["id"]!.DeepClone():null,["message"]=status=="conflict"?"成交编号已存在，但内容不同或有多个来源，请核对平台确认单。":duplicate?"内容一致，已入账，本批次会跳过。":"待入账"});
                if(status=="new")fresh.Add(index);
            }
            return new JsonObject{["state"]=state,["snapshot"]=AnalysisSnapshotRepository.Create(sql,user)};
        });
        var conflicts=rows.OfType<JsonObject>().Where(row=>row["status"]!.GetValue<string>()=="conflict").ToArray();var duplicateCount=rows.Count-fresh.Count-conflicts.Length;
        if(conflicts.Length>0 || fresh.Count==0)return new JsonObject{["status"]=conflicts.Length>0?"blocked":"already_recorded",["preview_id"]=null,["rows"]=rows,["new_count"]=fresh.Count,["duplicate_count"]=duplicateCount,["blockers"]=new JsonArray(conflicts.Select(row=>(JsonNode)JsonValue.Create($"第 {row["row"]} 行：{row["message"]}")!).ToArray())};
        var trades=new JsonArray(fresh.Select(index=>(JsonNode)Plan(items[index])).ToArray());
        var result=(await compute.Run(user,"ledger.replay",new {snapshot=input["snapshot"],transactions=trades,validation_nonce=Guid.NewGuid().ToString("N")},cancellation,seconds:180))!.AsObject();
        if(result["status"]!.GetValue<string>()!="ready") {result["preview_id"]=null;result["rows"]=rows;result["new_count"]=fresh.Count;result["duplicate_count"]=duplicateCount;return result;}
        ValidateResult(result);
        var id=Guid.NewGuid().ToString("N");var plan=new JsonObject{["transactions"]=trades,["reason"]=body.Reason.Trim(),["rows"]=rows.DeepClone(),["new_count"]=fresh.Count,["duplicate_count"]=duplicateCount};
        database.Write(user,sql=>
        {
            if(FinancialSnapshot.Hash(State(sql,user))!=FinancialSnapshot.Hash(input["state"]!))throw Conflict("账本或估值证据已变化，请重新预览。");
            sql.Execute("INSERT INTO ledger_replay_previews(user_id,preview_id,source_fingerprint,plan_json,result_json,created_at,expires_at) VALUES($1,$2,$3,$4::json,$5::json,now(),now()+interval '15 minutes')",user,id,FinancialSnapshot.Hash(input["state"]!),plan.ToJsonString(),result.ToJsonString());return 0;
        });
        return new JsonObject{["status"]="ready",["preview_id"]=id,["expires_at"]=DateTimeOffset.UtcNow.AddMinutes(15).ToOffset(TimeSpan.FromHours(8)).ToString("O"),["positions"]=result["positions"]!.DeepClone(),["valuations"]=result["valuations"]!.DeepClone(),["before_positions"]=input["state"]!["financial"]!["positions"]!.DeepClone(),["before_valuations"]=input["state"]!["financial"]!["portfolio_valuations"]!.DeepClone(),["rows"]=rows,["new_count"]=fresh.Count,["duplicate_count"]=duplicateCount,["blockers"]=new JsonArray(),["limitations"]=new JsonArray("原始成交和报告保留；本批新增成交、持仓及历史净值在同一事务中保存。","确认后请核对平台持仓、现金和费用；历史回算不计入前向模拟天数。")};
    }
    private static JsonObject Recorded(JsonNode preview,string id)
    {
        var response=new JsonObject{["status"]="recorded",["transaction_id"]=preview["transaction_id"]!.DeepClone(),["preview_id"]=id};
        if(preview["result_json"]?["transaction_ids"] is JsonArray ids)response["transaction_ids"]=ids.DeepClone();
        return response;
    }
    private static void ValidateResult(JsonObject result)
    {
        var positions=result["positions"]!.AsArray();if(positions.Count is <1 or >101 || positions.OfType<JsonObject>().Select(p=>p["symbol"]!.GetValue<string>()).Distinct().Count()!=positions.Count||positions.Count(p=>p!["asset_type"]!.GetValue<string>()=="cash")!=1)throw Conflict("历史回放持仓结果无效。");
        foreach(var row in positions.OfType<JsonObject>())if(Amount(row,"quantity")<0||Amount(row,"average_cost")<0||Amount(row,"market_value")<0)throw Conflict("历史回放不能产生负持仓或现金。");
        foreach(var row in result["valuations"]!.AsArray().OfType<JsonObject>())if(Amount(row,"total_value")<=0||Amount(row,"cash")<0||Amount(row,"unit_nav")<=0||Amount(row,"high_water_mark")<Amount(row,"unit_nav"))throw Conflict("历史回放净值无效。");
    }
    private static JsonNode Comparison(JsonObject result)
    {
        var clone=result.DeepClone();foreach(var key in new[]{"positions","valuations"})foreach(var row in clone[key]!.AsArray().OfType<JsonObject>()){row.Remove("updated_at");row.Remove("generated_at");}return clone;
    }
    public async Task<JsonObject> Confirm(string user,string id,bool confirmed,CancellationToken cancellation)
    {
        if(!confirmed)throw new ArgumentException("请明确确认历史补录");
        var input=database.Read(sql=>
        {
            var preview=sql.Query("SELECT * FROM ledger_replay_previews WHERE user_id=$1 AND preview_id=$2",user,id).FirstOrDefault()?.AsObject()??throw Conflict("补录预览不存在或不属于当前账号。");
            if(preview["consumed_at"] is null && DateTimeOffset.Parse(preview["expires_at"]!.GetValue<string>())<=DateTimeOffset.UtcNow)throw Conflict("补录预览已过期，请重新核对。");
            var state=State(sql,user);if(preview["consumed_at"] is null&&FinancialSnapshot.Hash(state)!=preview["source_fingerprint"]!.GetValue<string>())throw Conflict("账户、账本或估值证据已变化，请重新预览。");
            return new JsonObject{["preview"]=preview.DeepClone(),["state"]=state,["snapshot"]=AnalysisSnapshotRepository.Create(sql,user)};
        });
        var preview=input["preview"]!.AsObject();if(preview["consumed_at"] is not null)return Recorded(preview,id);
        var plan=preview["plan_json"]!.AsObject();var batch=plan["transactions"] is JsonArray;
        var trades=batch?plan["transactions"]!.AsArray():new JsonArray(plan["transaction"]!.DeepClone());
        // Confirmation must inspect the original evidence files again, even with unchanged SQL state.
        // A unique input bypasses compute-job result reuse; consumed previews still return above.
        var work=new JsonObject{["snapshot"]=input["snapshot"]!.DeepClone(),["validation_nonce"]=Guid.NewGuid().ToString("N")};work[batch?"transactions":"transaction"]=batch?trades.DeepClone():trades[0]!.DeepClone();
        var rebuilt=(await compute.Run(user,"ledger.replay",work,cancellation,seconds:180))!.AsObject();
        if(rebuilt["status"]!.GetValue<string>()!="ready")throw Conflict("历史事件证据不能再次验证，请重新核对。");ValidateResult(rebuilt);
        foreach(var key in new[]{"positions","valuations"})if(!JsonNode.DeepEquals(Comparison(rebuilt)[key],Comparison(preview["result_json"]!.AsObject())[key]))throw Conflict("重算结果与预览不一致，请重新核对。");
        var validatedTrades=trades.OfType<JsonObject>().Select(trade=>TransactionRules.Validate(Input(new HistoricalBody(trade["symbol"]!.GetValue<string>(),trade["asset_type"]!.GetValue<string>(),trade["action"]!.GetValue<string>(),Amount(trade,"quantity"),Amount(trade,"price"),Amount(trade,"fees"),DateTimeOffset.Parse(trade["traded_at"]!.GetValue<string>()),trade["external_id"]!.GetValue<string>(),plan["reason"]!.GetValue<string>(),trade["cash_symbol"]?.GetValue<string>(),trade["note"]?.GetValue<string>())))).ToArray();
        return database.Write(user,sql=>
        {
            var current=sql.Query("SELECT consumed_at,transaction_id,expires_at,result_json FROM ledger_replay_previews WHERE user_id=$1 AND preview_id=$2 FOR UPDATE",user,id).First()!;
            if(current["consumed_at"] is not null)return Recorded(current,id);
            if(DateTimeOffset.Parse(current["expires_at"]!.GetValue<string>())<=DateTimeOffset.UtcNow||FinancialSnapshot.Hash(State(sql,user))!=preview["source_fingerprint"]!.GetValue<string>())throw Conflict("账本或预览已变化，请重新核对。");
            var cash=rebuilt["positions"]!.AsArray().First(p=>p!["asset_type"]!.GetValue<string>()=="cash")!;
            var transactionIds=new List<long>();
            foreach(var validated in validatedTrades)transactionIds.Add(Convert.ToInt64(sql.Scalar("INSERT INTO transactions(user_id,symbol,asset_type,action,quantity,price,fees,traded_at,recorded_at,note,source,external_id,gross_amount,cash_delta,cash_symbol) VALUES($1,$2,$3,$4,$5,$6,$7,$8,now(),$9,'historical-manual',$10,$11,$12,$13) RETURNING id",user,validated.Symbol,validated.AssetType.ToString().ToLowerInvariant(),validated.Action.ToString().ToLowerInvariant(),validated.Quantity,validated.Price,validated.Fees,validated.TradedAt.UtcDateTime,validated.Note,validated.ExternalId,validated.GrossAmount,validated.CashDelta,cash["symbol"]!.GetValue<string>())));
            var transactionId=transactionIds[0];var audited=new HashSet<long>();
            sql.Execute("DELETE FROM positions WHERE user_id=$1",user);
            foreach(var row in rebuilt["positions"]!.AsArray().OfType<JsonObject>())sql.Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",user,row["symbol"]!.GetValue<string>(),row["asset_type"]!.GetValue<string>(),Amount(row,"quantity"),Amount(row,"average_cost"),Amount(row,"market_value"),DateOnly.Parse(row["as_of_date"]!.GetValue<string>()),DateTimeOffset.Parse(row["updated_at"]!.GetValue<string>()).UtcDateTime);
            var storedRunIds=input["state"]!["financial"]!["portfolio_valuations"]!.AsArray().Select(v=>v!["run_id"]!.GetValue<string>()).ToHashSet();
            if(!storedRunIds.SetEquals(rebuilt["valuations"]!.AsArray().Select(v=>v!["run_id"]!.GetValue<string>())))throw Conflict("历史回放不能增删估值日期。");
            foreach(var row in rebuilt["valuations"]!.AsArray().OfType<JsonObject>())sql.Execute("UPDATE portfolio_valuations SET total_value=$3,cash=$4,unit_nav=$5,high_water_mark=$6,net_external_flow=$7,previous_run_id=$8,positions_fingerprint=$9,events_fingerprint=$10,is_baseline=$11 WHERE user_id=$1 AND run_id=$2",user,row["run_id"]!.GetValue<string>(),Amount(row,"total_value"),Amount(row,"cash"),Amount(row,"unit_nav"),Amount(row,"high_water_mark"),Amount(row,"net_external_flow"),row["previous_run_id"]?.GetValue<string>(),row["positions_fingerprint"]!.GetValue<string>(),row["events_fingerprint"]!.GetValue<string>(),row["is_baseline"]!.GetValue<bool>());
            foreach(var row in rebuilt["transaction_audits"]!.AsArray().OfType<JsonObject>())
            {
                var target=sql.Scalar("SELECT id FROM transactions WHERE user_id=$1 AND source=$2 AND external_id=$3",user,row["source"]!.GetValue<string>(),row["external_id"]!.GetValue<string>())??throw Conflict("历史成交审计对象缺失。");
                var targetId=Convert.ToInt64(target);if(transactionIds.Contains(targetId) && !audited.Add(targetId))throw Conflict("补录成交审计重复。");
                sql.Execute("INSERT INTO audit_log(user_id,entity_type,entity_id,changed_at,before_json,after_json) VALUES($1,$2,$3,now(),$4::json,$5::json)",user,transactionIds.Contains(targetId)?"transaction":"transaction_replay",target.ToString(),row["before_json"]?.GetValue<string>(),row["after_json"]!.GetValue<string>());
            }
            if(!audited.SetEquals(transactionIds))throw Conflict("补录成交缺少逐笔审计。");
            var ids=new JsonArray(transactionIds.Select(value=>(JsonNode)JsonValue.Create(value)!).ToArray());
            sql.Execute("INSERT INTO audit_log(user_id,entity_type,entity_id,changed_at,before_json,after_json) VALUES($1,'ledger_replay',$2,now(),$3::json,$4::json)",user,id,input["state"]!["financial"]!.ToJsonString(),new JsonObject{["transaction_id"]=transactionId,["transaction_ids"]=ids.DeepClone(),["effective_date"]=validatedTrades.Min(value=>value.TradeDate).ToString("yyyy-MM-dd"),["reason"]=plan["reason"]!.DeepClone(),["positions"]=rebuilt["positions"]!.DeepClone(),["valuations"]=rebuilt["valuations"]!.DeepClone()}.ToJsonString());
            if(batch)rebuilt["transaction_ids"]=ids;
            sql.Execute("UPDATE ledger_replay_previews SET consumed_at=now(),transaction_id=$3,result_json=$4::json WHERE user_id=$1 AND preview_id=$2",user,id,transactionId,rebuilt.ToJsonString());
            return Recorded(new JsonObject{["transaction_id"]=transactionId,["result_json"]=rebuilt.DeepClone()},id);
        });
    }
    public JsonObject History(string user,int page,int limit)
    {if(page is <1 or >10000||limit is <1 or >100)throw new ArgumentException("分页参数无效");return database.Read(sql=>new JsonObject{["items"]=sql.Query("SELECT preview_id,created_at,consumed_at,transaction_id,plan_json FROM ledger_replay_previews WHERE user_id=$1 AND consumed_at IS NOT NULL ORDER BY consumed_at DESC,preview_id LIMIT $2 OFFSET $3",user,limit,(page-1)*limit),["total"]=Convert.ToInt64(sql.Scalar("SELECT count(*) FROM ledger_replay_previews WHERE user_id=$1 AND consumed_at IS NOT NULL",user))});}
    public JsonObject BatchHistory(string user,int page,int limit)
    {
        if(page is <1 or >10000||limit is <1 or >100)throw new ArgumentException("分页参数无效");
        return database.Read(sql=>new JsonObject{
            ["items"]=sql.Query("SELECT preview_id,consumed_at,plan_json->>'reason' AS reason,json_array_length(plan_json->'transactions') AS new_count,json_array_length(plan_json->'rows')-json_array_length(plan_json->'transactions') AS duplicate_count FROM ledger_replay_previews WHERE user_id=$1 AND consumed_at IS NOT NULL AND json_typeof(plan_json->'transactions')='array' ORDER BY consumed_at DESC,preview_id LIMIT $2 OFFSET $3",user,limit,(page-1)*limit),
            ["total"]=Convert.ToInt64(sql.Scalar("SELECT count(*) FROM ledger_replay_previews WHERE user_id=$1 AND consumed_at IS NOT NULL AND json_typeof(plan_json->'transactions')='array'",user))});
    }
    public JsonObject Receipt(string user,string id)=>database.Read(sql=>
    {
        var preview=sql.Query("SELECT preview_id,consumed_at,plan_json,result_json FROM ledger_replay_previews WHERE user_id=$1 AND preview_id=$2 AND consumed_at IS NOT NULL AND json_typeof(plan_json->'transactions')='array'",user,id).FirstOrDefault()
            ??throw new ApplicationFailure("not_found",404,"已入账的成交导入回执不存在。");
        // Use the frozen import outcome, even if today's ledger has subsequently changed.
        var plan=preview["plan_json"]!;var rows=plan["rows"]!.DeepClone().AsArray();var ids=preview["result_json"]!["transaction_ids"]!.AsArray();var index=0;
        if(rows.OfType<JsonObject>().Count(row=>row["status"]!.GetValue<string>()=="new")!=ids.Count)throw Conflict("导入回执记录不完整，请核对账本审计。");
        foreach(var row in rows.OfType<JsonObject>())if(row["status"]!.GetValue<string>()=="new")
        {row["transaction_id"]=ids[index++]!.DeepClone();row["message"]="本批已入账";}
        return new JsonObject{["preview_id"]=id,["consumed_at"]=preview["consumed_at"]!.DeepClone(),["reason"]=plan["reason"]!.DeepClone(),["rows"]=rows};
    });
}
