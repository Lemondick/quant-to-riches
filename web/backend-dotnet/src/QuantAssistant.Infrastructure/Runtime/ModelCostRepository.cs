using QuantAssistant.Core.Abstractions;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Runtime;

public sealed record ModelPrice(
    [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)] decimal InputPerMillion,
    [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)] decimal OutputPerMillion);
public sealed record CostSettings(long Revision, string Currency = "CNY",
    [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)] decimal? DailyBudget = null,
    Dictionary<string, ModelPrice>? Prices = null);
public sealed record ModelBillEvidence(
    [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)] decimal Amount,
    string Currency, string Reference, bool Confirmed = false);

public sealed partial class ModelCostRepository(AccountDatabase database)
{
    private static readonly JsonSerializerOptions Options = new(FinanceJson.Options);
    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")]
    private static partial Regex ModelPattern();
    [GeneratedRegex("^[A-Z]{3}$")]
    private static partial Regex CurrencyPattern();
    public static bool ValidModel(string value) => ModelPattern().IsMatch(value) && !value.Contains("://", StringComparison.Ordinal)
        && !value.StartsWith("sk-", StringComparison.OrdinalIgnoreCase) && !value.StartsWith("apikey_", StringComparison.OrdinalIgnoreCase);

    private static CostSettings Settings(IAccountSql sql, string userId) => sql.Scalar("SELECT payload::text FROM model_cost_settings WHERE user_id=$1 AND id=1", userId) is string json
        ? JsonSerializer.Deserialize<CostSettings>(json, Options)! : new(0, Prices: []);

    public JsonObject Read(string userId) => database.Read(sql => View(sql, userId, Settings(sql, userId)));
    public static JsonObject SummaryFor(IAccountSql sql,string userId)=>Summary(sql,userId,Settings(sql,userId));
    public static JsonObject EvidenceReadinessFor(IAccountSql sql,string userId)
    {
        var unknown=Convert.ToInt64(sql.Scalar("SELECT count(*) FROM model_call_costs WHERE user_id=$1 AND cost IS NULL AND status<>'reserved'",userId));
        var pending=Convert.ToInt64(sql.Scalar("SELECT count(*) FROM model_call_costs WHERE user_id=$1 AND status='reserved'",userId));
        return new(){["passed"]=unknown==0&&pending==0,["status"]=unknown==0&&pending==0?"passed":"blocked",["unknown_calls"]=unknown,["pending_calls"]=pending,
            ["blockers"]=unknown>0?new JsonArray("model_bill_or_price_evidence_missing"):pending>0?new JsonArray("model_calls_in_progress"):new JsonArray()};
    }
    public static bool UnboundedBudgetEnabled(IAccountSql sql, string userId) => Settings(sql, userId).DailyBudget is not null;

    public JsonObject Save(string userId, CostSettings settings)
    {
        if (settings.Revision < 0 || !CurrencyPattern().IsMatch(settings.Currency)
            || settings.DailyBudget is <= 0 or > 1000000 || settings.Prices?.Count > 100
            || settings.Prices?.Any(p => !ValidModel(p.Key) || p.Value.InputPerMillion is < 0 or > 1000000 || p.Value.OutputPerMillion is < 0 or > 1000000) == true)
            throw new ArgumentException("费用设置无效");
        return database.WriteNonLedger(userId, sql =>
        {
            var current = Settings(sql, userId);
            var summary = Summary(sql, userId, current);
            if (settings.Revision != current.Revision || summary["pending_calls"]!.GetValue<int>() > 0
                || settings.Currency != current.Currency && summary["calls"]!.GetValue<int>() > 0)
                throw new ApplicationFailure("cost_settings_conflict", 409, "费用设置已变化或模型调用尚未结束，请重新读取后再保存。");
            settings = settings with { Revision = current.Revision + 1, Prices = settings.Prices ?? [] };
            sql.Execute("INSERT INTO model_cost_settings(user_id,id,payload) VALUES($1,1,$2::json) ON CONFLICT(user_id,id) DO UPDATE SET payload=EXCLUDED.payload", userId, JsonSerializer.Serialize(settings, Options));
            return View(sql, userId, settings);
        });
    }

    private static JsonObject View(IAccountSql sql, string userId, CostSettings settings) => new()
    {
        ["settings"] = JsonSerializer.SerializeToNode(settings, Options), ["today"] = Summary(sql, userId, settings),
        ["items"] = sql.Query("SELECT id,called_at,model,status,input_tokens,output_tokens,cost,currency,role,provider,stage,unknown_reason,bill_evidence_json FROM model_call_costs WHERE user_id=$1 ORDER BY called_at DESC,id DESC LIMIT 50", userId),
        ["basis"] = "provider_usage_and_user_prices", ["unknown_cost_is_zero"] = false,
    };

    public JsonObject RecordBill(string userId,string callId,ModelBillEvidence evidence)
    {
        if(!evidence.Confirmed || evidence.Amount is < 0 or > 1000000 || !CurrencyPattern().IsMatch(evidence.Currency)
            || string.IsNullOrWhiteSpace(evidence.Reference) || evidence.Reference.Length>500 || evidence.Reference.Any(char.IsControl))
            throw new ArgumentException("请确认账单金额、货币和可核对的账单依据。");
        return database.WriteNonLedger(userId,sql=>
        {
            var row=sql.Query("SELECT status,cost,currency,unknown_reason,bill_evidence_json FROM model_call_costs WHERE user_id=$1 AND id=$2",userId,callId).FirstOrDefault()
                ??throw new ApplicationFailure("model_call_not_found",404,"模型调用记录不存在。");
            if(row["bill_evidence_json"] is JsonObject prior && prior["amount"]?.ToString()==Money(evidence.Amount)
                && prior["currency"]?.ToString()==evidence.Currency && prior["reference"]?.ToString()==evidence.Reference.Trim())
                return View(sql,userId,Settings(sql,userId));
            if(row["cost"] is not null || row["status"]?.ToString() is not("completed" or "interrupted")
                || row["currency"]?.ToString()!=evidence.Currency)
                throw new ApplicationFailure("model_bill_conflict",409,"调用尚未结束、已有费用证据或账单货币不一致，请重新核对。");
            var proof=new JsonObject{["amount"]=Money(evidence.Amount),["currency"]=evidence.Currency,["reference"]=evidence.Reference.Trim(),
                ["recorded_at"]=DateTimeOffset.UtcNow.ToString("O"),["basis"]="user_confirmed_provider_bill",["original_unknown_reason"]=row["unknown_reason"]?.DeepClone()};
            sql.Execute("UPDATE model_call_costs SET cost=$3,unknown_reason=NULL,bill_evidence_json=$4::jsonb WHERE user_id=$1 AND id=$2",userId,callId,Money(evidence.Amount),proof.ToJsonString());
            sql.Execute("INSERT INTO audit_log(user_id,entity_type,entity_id,changed_at,after_json) VALUES($1,'model_bill_evidence',$2,now(),$3::json)",userId,callId,proof.ToJsonString());
            return View(sql,userId,Settings(sql,userId));
        });
    }

    private static JsonObject Summary(IAccountSql sql, string userId, CostSettings settings)
    {
        var rows = sql.Query("SELECT status,cost,reserved_cost FROM model_call_costs WHERE user_id=$1 AND (called_at AT TIME ZONE 'Asia/Shanghai')::date=$2", userId, Today());
        var known = 0m; var reserved = 0m; var unknown = 0; var pending = 0;
        foreach (var row in rows.OfType<JsonObject>())
        {
            if (row["cost"] is not null) known += decimal.Parse(row["cost"]!.GetValue<string>(), CultureInfo.InvariantCulture);
            if (row["status"]!.GetValue<string>() == "reserved")
            { pending++; if (row["reserved_cost"] is not null) reserved += decimal.Parse(row["reserved_cost"]!.GetValue<string>(), CultureInfo.InvariantCulture); }
            else if (row["cost"] is null) unknown++;
        }
        var budget = settings.DailyBudget;
        return new() { ["date"] = Today().ToString("yyyy-MM-dd"), ["currency"] = settings.Currency, ["calls"] = rows.Count,
            ["known_cost"] = Money(known), ["total_cost"] = unknown > 0 || pending > 0 ? null : Money(known),
            ["unknown_calls"] = unknown, ["pending_calls"] = pending, ["reserved_cost"] = Money(reserved),
            ["remaining_budget"] = budget is null || unknown > 0 ? null : Money(Math.Max(0, budget.Value - known - reserved)),
            ["budget_blocker"] = budget is null ? null : unknown > 0 ? "unknown_usage_or_price" : known + reserved >= budget ? "daily_budget" : null };
    }

    public string Reserve(string userId, string model, int? maxInputTokens, int? maxOutputTokens) => database.WriteNonLedger(userId, sql => Reserve(sql,userId,model,maxInputTokens,maxOutputTokens));

    private static string Reserve(IAccountSql sql, string userId, string model, int? maxInputTokens, int? maxOutputTokens)
    {
        if (!ValidModel(model) || maxInputTokens < 0 || maxOutputTokens < 0) throw new ArgumentException("模型预算参数无效");
        var settings = Settings(sql, userId); var summary = Summary(sql, userId, settings);
        var price = settings.Prices?.GetValueOrDefault(model);
        decimal? amount = price is null || maxInputTokens is null || maxOutputTokens is null ? null
            : (price.InputPerMillion * maxInputTokens.Value + price.OutputPerMillion * maxOutputTokens.Value) / 1000000;
        if (settings.DailyBudget is not null && (summary["budget_blocker"] is not null || amount is null
            || amount > decimal.Parse(summary["remaining_budget"]!.GetValue<string>(), CultureInfo.InvariantCulture)))
            throw new ApplicationFailure("model_budget_exceeded", 409, "费用额度不足或调用费用上限未知，未调用模型。");
        var id = Guid.NewGuid().ToString("N");
        sql.Execute("INSERT INTO model_call_costs(user_id,id,called_at,model,status,prices_json,currency,reserved_cost) VALUES($1,$2,now(),$3,'reserved',$4::json,$5,$6)",
            userId, id, model, price is null ? null : JsonSerializer.Serialize(price, Options), settings.Currency, amount);
        return id;
    }

    public string ReserveOwned(string userId, string jobId, int attempt, string owner, string model, int? maxInput, int? maxOutput, string role = "unknown", string provider = "unknown") => database.WriteNonLedger(userId, sql =>
    {
        if (role is not ("news" or "strategy" or "reviewer" or "jev" or "bull" or "bear" or "unknown") || provider is not ("compatible_api" or "jev" or "unknown"))
            throw new ArgumentException("模型调用角色无效");
        RequireLease(sql,userId,jobId,attempt,owner);
        sql.Execute("SELECT pg_advisory_xact_lock(hashtextextended('model:calls',0))");
        sql.Execute("UPDATE model_call_costs c SET status='interrupted',stage='interrupted',unknown_reason='lease_interrupted' WHERE status='reserved' AND job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM compute_jobs j JOIN users u ON u.id=j.user_id WHERE j.user_id=c.user_id AND j.job_id=c.job_id AND j.attempt=c.job_attempt AND j.owner_token=c.owner_token AND j.status='running' AND j.deadline>now() AND j.lease_expires_at>now() AND u.status='active')");
        if ((long)sql.Scalar("SELECT count(*) FROM model_call_costs WHERE status='reserved'")! >= 3)
            throw new ApplicationFailure("model_gate_busy",409,"模型调用并发已满，请稍后显式重试。");
        var id = Reserve(sql,userId,model,maxInput,maxOutput);
        sql.Execute("UPDATE model_call_costs SET job_id=$3,job_attempt=$4,owner_token=$5,role=$6,provider=$7,stage='request' WHERE user_id=$1 AND id=$2",userId,id,jobId,attempt,owner,role,provider);
        return id;
    });

    private static void RequireLease(IAccountSql sql, string userId, string jobId, int attempt, string owner)
    {
        if (sql.Scalar("SELECT 1 FROM compute_jobs j JOIN users u ON u.id=j.user_id WHERE j.user_id=$1 AND j.job_id=$2 AND j.attempt=$3 AND j.owner_token=$4 AND j.kind IN('advisor.report','agent.research') AND j.status='running' AND j.deadline>now() AND j.lease_expires_at>now() AND u.status='active' FOR UPDATE OF j",userId,jobId,attempt,owner) is null)
            throw new ApplicationFailure("worker_lease_expired",409,"任务已停止，不能继续模型调用。");
    }

    public void FinishOwned(string userId,string jobId,int attempt,string owner,string callId,long? input,long? output,string stage="response",string? unknownReason=null) => database.WriteNonLedger(userId,sql=>
    {
        RequireLease(sql,userId,jobId,attempt,owner);
        if (sql.Scalar("SELECT 1 FROM model_call_costs WHERE user_id=$1 AND id=$2 AND job_id=$3 AND job_attempt=$4 AND owner_token=$5",userId,callId,jobId,attempt,owner) is null)
            throw new ApplicationFailure("model_call_not_found",404,"未找到本任务的费用记录。");
        return Finish(sql,userId,callId,input,output,stage,unknownReason);
    });

    public void Finish(string userId, string callId, long? inputTokens, long? outputTokens) => database.WriteNonLedger(userId, sql => Finish(sql,userId,callId,inputTokens,outputTokens));
    private static int Finish(IAccountSql sql, string userId, string callId, long? inputTokens, long? outputTokens, string stage="response",string? unknownReason=null)
    {
        if (stage is not ("response" or "connect_timeout" or "read_timeout" or "timeout" or "http_error" or "invalid_response" or "interrupted" or "transport_error")
            || unknownReason is not (null or "no_usage" or "invalid_usage")) throw new ArgumentException("模型调用阶段无效");
        var row = sql.Query("SELECT prices_json FROM model_call_costs WHERE user_id=$1 AND id=$2 AND status='reserved'", userId, callId).FirstOrDefault();
        if (row is null) return 0;
        var price = row["prices_json"]?.Deserialize<ModelPrice>(Options);
        var valid = inputTokens is >= 0 and <= 2000000000 && outputTokens is >= 0 and <= 2000000000;
        decimal? cost = price is null || !valid ? null : (price.InputPerMillion * inputTokens!.Value + price.OutputPerMillion * outputTokens!.Value) / 1000000;
        var reason = valid ? price is null ? "missing_price" : null : unknownReason ?? (inputTokens is null && outputTokens is null ? "no_usage" : "invalid_usage");
        return sql.Execute("UPDATE model_call_costs SET status='completed',input_tokens=$3,output_tokens=$4,cost=$5,stage=$6,unknown_reason=$7 WHERE user_id=$1 AND id=$2 AND status='reserved'", userId, callId,
            valid ? inputTokens : null, valid ? outputTokens : null, cost,stage,reason);
    }

    private static DateOnly Today() => DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
    private static string Money(decimal value) => value.ToString(CultureInfo.InvariantCulture);
}
