using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;

namespace QuantAssistant.Api.Services;

public sealed class PhotoMigrationService(AccountDatabase database,ComputeService compute)
{
    public sealed record PhotoRow(string Symbol,string Quantity,string AverageCost,string MarketValue,string Name="");
    private static string Money(decimal amount)=>amount.ToString(CultureInfo.InvariantCulture);
    private static decimal Amount(JsonNode row,string key)=>decimal.Parse(row[key]!.GetValue<string>(),CultureInfo.InvariantCulture);
    private static decimal Read(string raw,int digits,bool zero=false)
    {
        if(raw.Length>40 || !decimal.TryParse(raw,NumberStyles.Float,CultureInfo.InvariantCulture,out var value)||value<0||!zero&&value==0||value>=10000000000000000m)throw new ArgumentException("基金数值无效或超出支持范围");
        var result=decimal.Round(value,digits,MidpointRounding.ToEven);if(!zero&&result<=0)throw new ArgumentException("基金数值低于支持精度");return result;
    }
    private static ApplicationFailure Conflict(string message)=>new("photo_import_conflict",409,message);
    private static JsonObject Session(AccountSql sql,string user,string id,string kind)
    {
        var row=sql.Query("SELECT kind,payload,source_fingerprint,expires_at,result FROM review_sessions WHERE user_id=$1 AND id=$2",user,id).FirstOrDefault()?.AsObject()??throw Conflict("预览不存在或不属于当前账户。");
        if(row["kind"]!.GetValue<string>()!=kind)throw Conflict("请使用对应入口生成的核对凭证。");
        if(row["result"] is null && DateTimeOffset.Parse(row["expires_at"]!.GetValue<string>())<=DateTimeOffset.UtcNow)throw Conflict("预览已过期，请重新核对。");return row;
    }
    private static void Store(AccountSql sql,string user,string id,string kind,JsonObject value,string? fingerprint=null)
    {
        sql.Execute("DELETE FROM review_sessions WHERE user_id=$1 AND expires_at<=now() AND result IS NULL",user);
        if((long)sql.Scalar("SELECT count(*) FROM review_sessions WHERE user_id=$1 AND expires_at>now() AND result IS NULL",user)!>=20)throw Conflict("待核对记录过多，请稍后重试。");
        sql.Execute("INSERT INTO review_sessions(user_id,id,kind,payload,source_fingerprint,expires_at) VALUES($1,$2,$3,$4::jsonb,$5,now()+interval '15 minutes')",user,id,kind,value.ToJsonString(),fingerprint);
    }
    private static JsonObject State(AccountSql sql,string user,bool holdings)=>holdings ? new()
    {
        ["account"]=sql.Query("SELECT platform,status FROM portfolio_accounts WHERE user_id=$1 AND account_id='fund-photo'",user),
        ["positions"]=sql.Query("SELECT symbol,asset_type,quantity,average_cost,market_value,as_of_date FROM account_positions WHERE user_id=$1 AND account_id='fund-photo' ORDER BY symbol",user),
    }:new()
    {
        ["positions"]=sql.Query("SELECT symbol,asset_type,quantity,average_cost,market_value,as_of_date FROM positions WHERE user_id=$1 ORDER BY symbol",user),
        ["events"]=sql.Query("SELECT event_id,event_type,effective_date,amount,status,recorded_at FROM portfolio_events WHERE user_id=$1 ORDER BY event_id",user),
        ["valuations"]=sql.Query("SELECT run_id,as_of_date,total_value,generated_at FROM portfolio_valuations WHERE user_id=$1 ORDER BY as_of_date,run_id",user),
        ["transactions"]=sql.Query("SELECT id,symbol,quantity,price,fees,traded_at,source,external_id,reversed_by_correction_id FROM transactions WHERE user_id=$1 ORDER BY id",user),
    };
    private static string Hash(JsonNode value)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value.ToJsonString()))).ToLowerInvariant();
    private static string Signature(PhotoRow[] rows,string? cash,string mode,string flow,bool holdings)
    {
        var values=new JsonArray();foreach(var row in rows.OrderBy(r=>r.Symbol.Trim(),StringComparer.Ordinal))values.Add(new JsonObject{["symbol"]=row.Symbol.Trim(),["quantity"]=Read(row.Quantity,8).ToString("G29",CultureInfo.InvariantCulture),["average_cost"]=Read(row.AverageCost,6).ToString("G29",CultureInfo.InvariantCulture),["market_value"]=Read(row.MarketValue,4).ToString("G29",CultureInfo.InvariantCulture)});
        return Hash(new JsonObject{["positions"]=values,["cash"]=holdings?null:Read(cash??"",4,true).ToString("G29",CultureInfo.InvariantCulture),["mode"]=mode,["flow"]=ReadSigned(flow).ToString("G29",CultureInfo.InvariantCulture),["holdings"]=holdings});
    }
    public async Task<JsonObject> Confirm(string user,string previewId,PhotoRow[] rows,string cash,CancellationToken cancellation)
    {
        var signature=Signature(rows,cash,"initial","0",false);
        var previous=database.Read(sql=>Session(sql,user,previewId,"photo-preview")["result"]?.DeepClone());
        if(previous is not null)
        {if(previous["request_signature"]!.GetValue<string>()!=signature)throw Conflict("同一预览已提交不同内容，请重新生成预览。");return previous["result"]!.DeepClone().AsObject();}
        try
        {
            var review=await Review(user,previewId,rows,cash,"initial","0",false,cancellation);
            return Commit(user,review["review_id"]!.GetValue<string>(),true,false);
        }
        catch(ApplicationFailure failure) when(failure.Code=="photo_import_conflict")
        {
            // An identical concurrent confirmation may commit while metadata is
            // being checked. Return its durable result instead of a state conflict.
            var saved=database.Read(sql=>Session(sql,user,previewId,"photo-preview")["result"]?.DeepClone());
            if(saved?["request_signature"]?.GetValue<string>()==signature)return saved["result"]!.DeepClone().AsObject();
            throw;
        }
    }
    public JsonObject Manual(string user,DateOnly day)
    {
        if(day>FundFeeRepository.Today())throw new ArgumentException("日期不能晚于今天");
        var id=Guid.NewGuid().ToString("N");var result=new JsonObject{["preview_id"]=id,["as_of_date"]=day.ToString("yyyy-MM-dd"),["positions"]=new JsonArray(),["cash"]="",["warnings"]=new JsonArray("请录入同一账户的全部基金与现金；基金代码将在差异预览时核验。"),["provider"]="人工录入",["inferred"]=false,["requires_cash"]=true,["expires_in_seconds"]=900,["image_sha256"]=Guid.NewGuid().ToString("N")};
        database.WriteNonLedger(user,sql=>{Store(sql,user,id,"photo-preview",result);return 0;});return result;
    }
    public async Task<JsonObject> Recognize(string user,string image,DateOnly? day,bool consent,bool holdings,CancellationToken cancellation)
    {
        if(!consent||image.Length>8*1024*1024||day>FundFeeRepository.Today()||day is null&&!holdings)throw new ArgumentException("请确认OCR授权、截图日期和图片大小");
        byte[] decoded;try{decoded=Convert.FromBase64String(image);}catch(FormatException){throw new ArgumentException("图片编码无效");}
        if(decoded.Length is <12 or >6*1024*1024||!(decoded.AsSpan(0,8).SequenceEqual(new byte[]{137,80,78,71,13,10,26,10})||decoded[0]==255&&decoded[1]==216&&decoded[2]==255||Encoding.ASCII.GetString(decoded,0,4)=="RIFF"&&Encoding.ASCII.GetString(decoded,8,4)=="WEBP"))throw new ArgumentException("仅支持不超过6MiB的PNG、JPEG或WebP图片");
        var result=(await compute.Run(user,"photo.recognize",new{image_base64=image,as_of_date=day,holdings_only=holdings},cancellation,accountSnapshot:true,seconds:120))!.AsObject();
        var id=result["preview_id"]!.GetValue<string>();database.WriteNonLedger(user,sql=>{Store(sql,user,id,"photo-preview",result);return 0;});return result;
    }
    public JsonObject Merge(string user,string[] ids)=>database.WriteNonLedger(user,sql=>
    {
        if(ids.Length is <1 or >6)throw new ArgumentException("每次合并1至6张截图");
        var previews=ids.Select(id=>Session(sql,user,id,"photo-preview")["payload"]!.AsObject()).ToArray();
        if(previews.Select(p=>p["as_of_date"]!.GetValue<string>()).Distinct().Count()!=1)throw Conflict("不同日期截图不能合并。");
        var positions=new Dictionary<string,JsonObject>();var warnings=new JsonArray();
        foreach(var preview in previews)
        {
            foreach(var warning in preview["warnings"]!.AsArray())warnings.Add(warning!.DeepClone());
            foreach(var row in preview["positions"]!.AsArray().OfType<JsonObject>())
            {var symbol=row["symbol"]!.GetValue<string>();if(positions.TryGetValue(symbol,out var old))warnings.Add(JsonNode.DeepEquals(old,row)?$"{symbol}重复行已去重，不累加份额或金额。":$"{symbol}多图数值冲突，暂保留第一张数值，必须人工核对修改。");else positions[symbol]=row.DeepClone().AsObject();}
        }
        var result=previews[0].DeepClone().AsObject();var id=Guid.NewGuid().ToString("N");result["preview_id"]=id;result["positions"]=new JsonArray(positions.Values.Select(p=>(JsonNode?)p).ToArray());result["warnings"]=warnings;result["image_sha256"]=Hash(new JsonArray(previews.Select(p=>p["image_sha256"]!.DeepClone()).ToArray()));
        Store(sql,user,id,"photo-preview",result);return result;
    });
    public async Task<JsonObject> Review(string user,string previewId,PhotoRow[] body,string? cash,string mode,string externalFlow,bool holdings,CancellationToken cancellation)
    {
        if(body.Length is <1 or >100||body.Select(r=>r.Symbol.Trim()).Distinct().Count()!=body.Length||body.Any(r=>r.Symbol.Trim().Length!=6||r.Symbol.Trim().Any(c=>!char.IsAsciiDigit(c)))||mode is not("initial" or "calibration"))throw new ArgumentException("基金行数、代码或导入模式无效");
        var preview=database.Read(sql=>Session(sql,user,previewId,"photo-preview")["payload"]!.DeepClone().AsObject());var day=DateOnly.Parse(preview["as_of_date"]!.GetValue<string>());
        var positions=new JsonArray();
        foreach(var row in body.OrderBy(r=>r.Symbol,StringComparer.Ordinal))positions.Add(new JsonObject{["symbol"]=row.Symbol.Trim(),["asset_type"]="fund",["quantity"]=Money(Read(row.Quantity,8)),["average_cost"]=Money(Read(row.AverageCost,6)),["market_value"]=Money(Read(row.MarketValue,4)),["as_of_date"]=day.ToString("yyyy-MM-dd")});
        var metadata=(await compute.Run(user,"photo.identify",new{positions,as_of_date=day},cancellation,accountSnapshot:true,seconds:90))!.AsArray();
        var names=new JsonObject();foreach(var row in metadata.OfType<JsonObject>())names[row["symbol"]!.GetValue<string>()]=row["name"]!.DeepClone();
        if(body.Any(r=>names[r.Symbol.Trim()] is null))throw Conflict("基金名称和代码未能核验，请重新核对。");
        var flow=holdings?0:ReadSigned(externalFlow);if(mode=="initial"&&flow!=0)throw new ArgumentException("首次建档净入金必须为0");
        if(!holdings){var balance=Read(cash??"",4,true);positions.Add(new JsonObject{["symbol"]="CASH.CNY",["asset_type"]="cash",["quantity"]=Money(balance),["average_cost"]="1",["market_value"]=Money(balance),["as_of_date"]=day.ToString("yyyy-MM-dd")});names["CASH.CNY"]="人民币现金";}
        return database.WriteNonLedger(user,sql=>
        {
            Session(sql,user,previewId,"photo-preview");var state=State(sql,user,holdings);var before=state["positions"]!.AsArray().OfType<JsonObject>().ToDictionary(row=>row["symbol"]!.GetValue<string>());
            ValidateState(state,day,mode,holdings);
            if(positions.OfType<JsonObject>().Any(p=>before.TryGetValue(p["symbol"]!.GetValue<string>(),out var old)&&DateOnly.Parse(old["as_of_date"]!.GetValue<string>())>day))throw Conflict("截图日期早于已保存记录，请使用最新截图。");
            var total=positions.OfType<JsonObject>().Sum(p=>Amount(p,"market_value"));if(total-flow<=0)throw new ArgumentException("扣除净入金后的账户价值必须为正");
            var differences=new JsonArray();var after=positions.OfType<JsonObject>().ToDictionary(p=>p["symbol"]!.GetValue<string>());
            foreach(var symbol in (holdings?after.Keys:after.Keys.Union(before.Keys)).Order(StringComparer.Ordinal))
            {
                var old=before.GetValueOrDefault(symbol);var next=after.GetValueOrDefault(symbol);
                var item=new JsonObject{["symbol"]=symbol,["change"]=old is null?"新增":next is null?"移除":new[]{"quantity","average_cost","market_value"}.All(key=>Amount(old,key)==Amount(next,key))?"保留":"修改"};
                foreach(var (key,label) in new[]{("quantity","quantity"),("average_cost","cost"),("market_value","value")}) {item["before_"+label]=old?[key]?.DeepClone()??JsonValue.Create("0");item["after_"+label]=next?[key]?.DeepClone()??JsonValue.Create("0");}differences.Add(item);
            }
            var id=Guid.NewGuid().ToString("N");var record=new JsonObject{["positions"]=positions,["names"]=names,["mode"]=mode,["flow"]=Money(flow),["preview_id"]=previewId,["image_sha256"]=preview["image_sha256"]!.DeepClone(),["inferred"]=preview["inferred"]!.DeepClone(),["request_signature"]=Signature(body,cash,mode,externalFlow,holdings)};
            Store(sql,user,id,holdings?"photo-holdings-review":"photo-account-review",record,Hash(state));
            return new JsonObject{["review_id"]=id,["differences"]=differences,["total"]=Money(total),["mode"]=mode,["names"]=names.DeepClone(),["external_flow"]=Money(flow),["scope"]=holdings?"fund_holdings":null,["complete_account"]=!holdings,["cash"]=null};
        });
    }
    private static decimal ReadSigned(string raw)
    {if(!decimal.TryParse(raw,NumberStyles.Float,CultureInfo.InvariantCulture,out var result)||Math.Abs(result)>=10000000000000000m||decimal.Round(result,8)!=result)throw new ArgumentException("净入金数值无效");return result;}
    private static void ValidateState(JsonObject state,DateOnly day,string mode,bool holdings)
    {
        if(holdings)
        {var account=state["account"]!.AsArray().FirstOrDefault();if(account is not null&&(account["platform"]!.GetValue<string>()!="photo_import"||account["status"]!.GetValue<string>()!="active"))throw Conflict("截图持仓账户标识已被占用或归档。");return;}
        if(state["positions"]!.AsArray().Any(p=>p!["asset_type"]!.GetValue<string>() is not("fund" or "cash")||DateOnly.Parse(p["as_of_date"]!.GetValue<string>())>day))throw Conflict("账户含其他资产或截图日期早于当前持仓，不能覆盖。");
        var valuations=state["valuations"]!.AsArray();
        if(mode=="initial"&&(valuations.Count>0||state["transactions"]!.AsArray().Count>0||state["events"]!.AsArray().Any(e=>e!["event_type"]!.GetValue<string>()=="baseline"&&e["status"]!.GetValue<string>()=="confirmed")))throw Conflict("账户已有记录，请选择已有账户校准。");
        if(mode=="calibration"&&(valuations.Count==0||valuations.Any(v=>DateOnly.Parse(v!["as_of_date"]!.GetValue<string>())>=day)))throw Conflict("校准需要已有有效净值，且截图日期必须晚于最近估值日。");
    }
    public JsonObject Commit(string user,string id,bool confirmed,bool holdings)
    {
        if(!confirmed)throw new ArgumentException("请核对差异并明确确认");
        return database.WriteNonLedger(user,sql=>
        {
            var session=Session(sql,user,id,holdings?"photo-holdings-review":"photo-account-review");if(session["result"] is JsonObject existing)return existing;
            var record=session["payload"]!.AsObject();var positions=record["positions"]!.AsArray();var day=DateOnly.Parse(positions[0]!["as_of_date"]!.GetValue<string>());var mode=record["mode"]!.GetValue<string>();var state=State(sql,user,holdings);
            var parent=Session(sql,user,record["preview_id"]!.GetValue<string>(),"photo-preview");
            if(parent["result"] is JsonObject saved)
            {if(saved["request_signature"]!.GetValue<string>()!=record["request_signature"]!.GetValue<string>())throw Conflict("同一预览已提交不同内容。");var same=saved["result"]!.DeepClone().AsObject();sql.Execute("UPDATE review_sessions SET result=$3::jsonb WHERE user_id=$1 AND id=$2",user,id,same.ToJsonString());return same;}
            if(Hash(state)!=session["source_fingerprint"]!.GetValue<string>())throw Conflict("账户已变化，请重新生成差异预览。");ValidateState(state,day,mode,holdings);
            var eventId=(holdings?"photo:":"photo-review:")+Hash(record);var names=record["names"]!.AsObject();
            if(holdings)sql.Execute("INSERT INTO portfolio_accounts(user_id,account_id,name,platform,currency,status) VALUES($1,'fund-photo','截图基金持仓','photo_import','CNY','active') ON CONFLICT(user_id,account_id) DO NOTHING",user);
            else sql.Execute("DELETE FROM positions WHERE user_id=$1",user);
            foreach(var row in positions.OfType<JsonObject>())
            {
                var symbol=row["symbol"]!.GetValue<string>();var type=row["asset_type"]!.GetValue<string>();var name=names[symbol]!.GetValue<string>();
                sql.Execute("INSERT INTO instruments(user_id,symbol,asset_type,name,as_of_date,updated_at) VALUES($1,$2,$3,$4,$5,now()) ON CONFLICT(user_id,symbol) DO NOTHING",user,symbol,type,name,day);
                if(holdings)sql.Execute("INSERT INTO account_positions(user_id,account_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at) VALUES($1,'fund-photo',$2,'fund',$3,$4,$5,$6,now()) ON CONFLICT(user_id,account_id,symbol) DO UPDATE SET quantity=EXCLUDED.quantity,average_cost=EXCLUDED.average_cost,market_value=EXCLUDED.market_value,as_of_date=EXCLUDED.as_of_date,updated_at=now()",user,symbol,Amount(row,"quantity"),Amount(row,"average_cost"),Amount(row,"market_value"),day);
                else sql.Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,now())",user,symbol,type,Amount(row,"quantity"),Amount(row,"average_cost"),Amount(row,"market_value"),day);
            }
            JsonObject result;
            if(holdings)
            {
                sql.Execute("INSERT INTO account_events(user_id,account_id,event_id,event_type,effective_date,reason,details_json,recorded_at) VALUES($1,'fund-photo',$2,'snapshot',$3,'用户确认截图基金持仓；仅更新图片中的基金，不构成完整账户基线',$4::json,now()) ON CONFLICT(user_id,account_id,event_id) DO NOTHING",user,eventId,day,new JsonObject{["scope"]="fund_holdings",["complete_account"]=false,["cash_known"]=false,["names"]=names.DeepClone(),["inferred"]=record["inferred"]!.DeepClone(),["image_sha256"]=record["image_sha256"]!.DeepClone()}.ToJsonString());
                result=new(){["status"]="saved",["scope"]="fund_holdings",["account_id"]="fund-photo",["updated_count"]=positions.Count,["cash"]=null,["complete_account"]=false,["readiness"]="observation"};
            }
            else
            {
                var reason=mode=="initial"?"用户核对完整基金与现金差异后首次建档":"用户核对完整基金与现金差异后校准账户";
                sql.Execute("INSERT INTO portfolio_events(user_id,event_id,event_type,effective_date,amount,status,reason,details_json,recorded_at) VALUES($1,$2,$3,$4,$5,'confirmed',$6,$7::json,now())",user,eventId,mode=="initial"?"baseline":"calibration",day,mode=="initial"?(decimal?)null:Amount(record,"flow"),reason,new JsonObject{["positions"]=positions.DeepClone(),["net_external_flow"]=record["flow"]!.DeepClone(),["source"]="account_snapshot_import"}.ToJsonString());
                sql.Execute("INSERT INTO audit_log(user_id,entity_type,entity_id,changed_at,before_json,after_json) VALUES($1,$2,$3,now(),$4::json,$5::json)",user,mode=="initial"?"portfolio_account_import":"portfolio_calibration",eventId,state["positions"]!.ToJsonString(),new JsonObject{["event_id"]=eventId,["as_of_date"]=day.ToString("yyyy-MM-dd"),["positions"]=positions.DeepClone(),["reason"]=reason,["net_external_flow"]=record["flow"]!.DeepClone()}.ToJsonString());
                result=new(){["event_id"]=eventId,["status"]="imported"};
            }
            sql.Execute("UPDATE review_sessions SET result=$3::jsonb WHERE user_id=$1 AND id=$2",user,id,result.ToJsonString());
            sql.Execute("UPDATE review_sessions SET result=$3::jsonb WHERE user_id=$1 AND id=$2",user,record["preview_id"]!.GetValue<string>(),new JsonObject{["request_signature"]=record["request_signature"]!.DeepClone(),["result"]=result.DeepClone()}.ToJsonString());return result;
        });
    }
    public JsonObject Holdings(string user)=>database.Read(sql=>
    {
        var names=new JsonObject();foreach(var row in sql.Query("SELECT details_json FROM account_events WHERE user_id=$1 AND account_id='fund-photo' ORDER BY recorded_at ASC,id ASC LIMIT 500",user).OfType<JsonObject>())if(row["details_json"]?["names"] is JsonObject map)foreach(var (key,value) in map)names[key]=value?.DeepClone();
        var rows=sql.Query("SELECT symbol,quantity,average_cost,market_value,as_of_date FROM account_positions WHERE user_id=$1 AND account_id='fund-photo' ORDER BY symbol",user);foreach(var row in rows.OfType<JsonObject>())row["name"]=names[row["symbol"]!.GetValue<string>()]?.DeepClone()??row["symbol"]!.DeepClone();
        return new JsonObject{["scope"]="fund_holdings",["complete_account"]=false,["cash"]=null,["items"]=rows};
    });
}
