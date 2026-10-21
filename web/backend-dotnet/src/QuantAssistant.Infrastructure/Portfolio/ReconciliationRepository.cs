using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Portfolio;

public sealed record CompareAccounts(string[] AccountIds,bool CompleteScope=false,decimal? ExpectedFees=null,DateOnly? Start=null,DateOnly? End=null,string Evidence="");
public sealed class ReconciliationRepository(AccountDatabase database)
{
    private static decimal Amount(JsonNode row,string key)=>decimal.Parse(row[key]!.GetValue<string>(),CultureInfo.InvariantCulture);
    private static string? Money(decimal? amount)=>amount?.ToString(CultureInfo.InvariantCulture);
    private static ApplicationFailure Conflict(string message)=>new("ledger_review_conflict",409,message);
    private static JsonObject Source(AccountSql sql,string user,string[] accounts)=>new()
    {
        ["local"]=sql.Query("SELECT symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at FROM positions WHERE user_id=$1 ORDER BY symbol",user),
        ["accounts"]=sql.Query("SELECT account_id,name,platform,currency,status FROM portfolio_accounts WHERE user_id=$1 AND account_id=ANY($2) ORDER BY account_id",user,accounts),
        ["snapshots"]=sql.Query("SELECT account_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date FROM account_positions WHERE user_id=$1 AND account_id=ANY($2) ORDER BY account_id,symbol",user,accounts),
        ["fees"]=sql.Query("SELECT id,source,external_id,fees,traded_at,reversed_by_correction_id FROM transactions WHERE user_id=$1 ORDER BY id",user),
        ["dividends"]=sql.Query("SELECT id,fees,paid_at,reversed_by_correction_id FROM dividend_events WHERE user_id=$1 ORDER BY id",user),
        ["actions"]=sql.Query("SELECT id,fees,effective_at FROM corporate_action_events WHERE user_id=$1 ORDER BY id",user),
    };
    private static string Hash(JsonNode node)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(node.ToJsonString()))).ToLowerInvariant();
    public JsonObject Accounts(string user)=>database.Read(sql=>new JsonObject{["items"]=sql.Query("SELECT account_id,name,platform,currency,status FROM portfolio_accounts WHERE user_id=$1 AND status='active' ORDER BY account_id",user)});
    public JsonObject Compare(string user,CompareAccounts body)
    {
        if(body.AccountIds.Length is <1 or >30 || body.AccountIds.Distinct().Count()!=body.AccountIds.Length || body.AccountIds.Any(id=>string.IsNullOrWhiteSpace(id)||id.Length>200) || body.Start>body.End || body.ExpectedFees<0 || body.Evidence.Length>500 || body.ExpectedFees is not null && (body.Start is null || body.End is null || string.IsNullOrWhiteSpace(body.Evidence))) throw new ArgumentException("对账范围或费用依据无效");
        return database.WriteNonLedger(user,sql=>
        {
            var source=Source(sql,user,body.AccountIds);
            var accounts=source["accounts"]!.AsArray();if(accounts.Count!=body.AccountIds.Length || accounts.Any(a=>a!["status"]!.GetValue<string>()!="active"||a["currency"]!.GetValue<string>()!="CNY")) throw Conflict("只可对比已保存且启用的人民币账户。");
            var local=source["local"]!.AsArray().OfType<JsonObject>().ToDictionary(r=>r["symbol"]!.GetValue<string>());
            var snapshots=source["snapshots"]!.AsArray().OfType<JsonObject>().ToArray();var blockers=new JsonArray();
            if(!body.CompleteScope) blockers.Add("scope_unconfirmed");
            if(snapshots.Length==0 || body.AccountIds.Any(id=>!snapshots.Any(r=>r["account_id"]!.GetValue<string>()==id))) blockers.Add("snapshot_missing");
            var dates=snapshots.Select(r=>r["as_of_date"]!.GetValue<string>()).ToHashSet();
            if(dates.Count!=1 || !local.Values.Select(r=>r["as_of_date"]!.GetValue<string>()).ToHashSet().SetEquals(dates)) blockers.Add("snapshot_dates_differ");
            var aggregate=snapshots.GroupBy(r=>r["symbol"]!.GetValue<string>()).ToDictionary(g=>g.Key,g=>
            {
                var types=g.Select(r=>r["asset_type"]!.GetValue<string>()).Distinct().ToArray();if(types.Length!=1) throw Conflict("相同代码资产类型冲突，请核对原始快照。");
                return new {Type=types[0],Quantity=g.Sum(r=>Amount(r,"quantity")),Value=g.Sum(r=>Amount(r,"market_value")),Cost=g.Sum(r=>Amount(r,"quantity")*Amount(r,"average_cost")),Known=g.All(r=>Amount(r,"average_cost")>0)};
            });
            var items=new JsonArray();var known=body.CompleteScope&&snapshots.Length>0;
            foreach(var symbol in local.Keys.Union(aggregate.Keys).Order(StringComparer.Ordinal))
            {
                var left=local.GetValueOrDefault(symbol);var right=aggregate.GetValueOrDefault(symbol);
                if(left is not null && right is not null && left["asset_type"]!.GetValue<string>()!=right.Type) throw Conflict("主账本和平台快照的相同代码资产类型冲突。");
                decimal? quantityLeft=left is not null?Amount(left,"quantity"):known?0:null,quantityRight=right is not null?right.Quantity:known?0:null;
                decimal? valueLeft=left is not null?Amount(left,"market_value"):known?0:null,valueRight=right is not null?right.Value:known?0:null;
                decimal? costLeft=left is not null && Amount(left,"average_cost")>0?Amount(left,"quantity")*Amount(left,"average_cost"):known&&(left is null||Amount(left,"quantity")==0)?0:null;
                decimal? costRight=right is not null && right.Known?right.Cost:known&&(right is null||right.Quantity==0)?0:null;
                items.Add(new JsonObject{["symbol"]=symbol,["asset_type"]=left?["asset_type"]?.GetValue<string>()??right!.Type,
                    ["ledger_quantity"]=Money(quantityLeft),["platform_quantity"]=Money(quantityRight),["quantity_delta"]=Money(quantityRight-quantityLeft),
                    ["ledger_value"]=Money(valueLeft),["platform_value"]=Money(valueRight),["value_delta"]=blockers.Count==0?Money(valueRight-valueLeft):null,
                    ["ledger_cost"]=Money(costLeft),["platform_cost"]=Money(costRight),["cost_delta"]=Money(costRight-costLeft)});
            }
            if(items.Any(item=>item!["ledger_cost"] is null||item["platform_cost"] is null)) blockers.Add("cost_evidence_missing");
            var actual=0m;
            if(body.Start is not null && body.End is not null)
                foreach(var (key,dateColumn) in new[]{("fees","traded_at"),("dividends","paid_at"),("actions","effective_at")})
                    foreach(var row in source[key]!.AsArray().OfType<JsonObject>())
                    {var day=DateOnly.FromDateTime(DateTimeOffset.Parse(row[dateColumn]!.GetValue<string>()).ToOffset(TimeSpan.FromHours(8)).DateTime);if(row["reversed_by_correction_id"] is null&&day>=body.Start&&day<=body.End) actual+=Amount(row,"fees");}
            var feeStatus=body.ExpectedFees is null?"unknown":body.ExpectedFees==actual?"matched":"different";if(feeStatus=="unknown") blockers.Add("fee_evidence_missing");
            bool Different(JsonNode row,string key,decimal tolerance)=>row[key] is not null && Math.Abs(Amount(row,key))>tolerance;
            var different=items.OfType<JsonObject>().Any(row=>Different(row,"quantity_delta",0)||Different(row,"cost_delta",.01m)||Different(row,"value_delta",.01m));
            var id=Guid.NewGuid().ToString("N");var result=new JsonObject{["reconciliation_id"]=id,["account_ids"]=new JsonArray(body.AccountIds.Select(id=>(JsonNode?)JsonValue.Create(id)).ToArray()),["complete_scope"]=body.CompleteScope,
                ["created_at"]=DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(8)).ToString("O"),["items"]=items,["blockers"]=blockers,["status"]=different||feeStatus=="different"?"differences":blockers.Count>0?"needs_review":"matched",
                ["fees"]=new JsonObject{["start"]=body.Start?.ToString("yyyy-MM-dd"),["end"]=body.End?.ToString("yyyy-MM-dd"),["ledger_total"]=body.Start is not null && body.End is not null?Money(actual):null,["platform_total"]=Money(body.ExpectedFees),["difference"]=Money(body.ExpectedFees-actual),["evidence"]=body.Evidence,["status"]=feeStatus},
                ["limitations"]=new JsonArray("未提供平台费用证据时，费用差异显示未知。","比较和处理说明不会改动持仓、现金或交易记录。")};
            sql.Execute("INSERT INTO ledger_reconciliations(user_id,reconciliation_id,created_at,source_fingerprint,result_json) VALUES($1,$2,now(),$3,$4::json)",user,id,Hash(source),result.ToJsonString());
            Audit(sql,user,"ledger_reconciliation",id,new(){["account_ids"]=result["account_ids"]!.DeepClone(),["status"]=result["status"]!.DeepClone()});return result;
        });
    }
    public JsonObject Resolve(string user,string id,string resolution,string note)
    {
        if(resolution is not("explained" or "follow_up" or "matched")||string.IsNullOrWhiteSpace(note)||note.Length>1000)throw new ArgumentException("处理结论或说明无效");
        return database.WriteNonLedger(user,sql=>
        {
            var row=sql.Query("SELECT source_fingerprint,result_json FROM ledger_reconciliations WHERE user_id=$1 AND reconciliation_id=$2",user,id).FirstOrDefault()??throw Conflict("对账记录不存在。");
            var result=row["result_json"]!.AsObject();var accounts=result["account_ids"]!.AsArray().Select(n=>n!.GetValue<string>()).ToArray();
            if(Hash(Source(sql,user,accounts))!=row["source_fingerprint"]!.GetValue<string>())throw Conflict("账本或平台快照已变化，请重新对账后处理。");
            if(resolution=="matched"&&result["status"]!.GetValue<string>()!="matched")throw Conflict("仍存在差异或证据缺项，请记录解释或待核对。");
            sql.Execute("UPDATE ledger_reconciliations SET resolution=$3,resolution_note=$4,resolved_at=now() WHERE user_id=$1 AND reconciliation_id=$2",user,id,resolution,note.Trim());
            Audit(sql,user,"reconciliation_resolution",id,new(){["resolution"]=resolution,["note"]=note.Trim()});return new JsonObject{["status"]="recorded",["reconciliation_id"]=id};
        });
    }
    private static void Audit(AccountSql sql,string user,string kind,string id,JsonObject value)=>sql.Execute("INSERT INTO audit_log(user_id,entity_type,entity_id,changed_at,after_json) VALUES($1,$2,$3,now(),$4::json)",user,kind,id,value.ToJsonString());
    public JsonObject Detail(string user,string id)=>database.Read(sql=>
    {
        var row=sql.Query("SELECT source_fingerprint,result_json,resolution,resolution_note,resolved_at FROM ledger_reconciliations WHERE user_id=$1 AND reconciliation_id=$2",user,id).FirstOrDefault()
            ??throw new ApplicationFailure("reconciliation_not_found",404,"对账记录不存在。");
        var result=row["result_json"]!.DeepClone().AsObject();
        var accounts=result["account_ids"]!.AsArray().Select(n=>n!.GetValue<string>()).ToArray();
        result["can_resolve"]=Hash(Source(sql,user,accounts))==row["source_fingerprint"]!.GetValue<string>();
        foreach(var key in new[]{"resolution","resolution_note","resolved_at"}) result[key]=row[key]?.DeepClone();
        return result;
    });
    public JsonObject History(string user,int page,int limit)
    {if(page is <1 or >10000 || limit is <1 or >100)throw new ArgumentException("分页参数无效");return database.Read(sql=>new JsonObject{["items"]=sql.Query("SELECT reconciliation_id,created_at,result_json,resolution,resolution_note,resolved_at FROM ledger_reconciliations WHERE user_id=$1 ORDER BY created_at DESC,reconciliation_id LIMIT $2 OFFSET $3",user,limit,(page-1)*limit),["total"]=Convert.ToInt64(sql.Scalar("SELECT count(*) FROM ledger_reconciliations WHERE user_id=$1",user))});}
}
