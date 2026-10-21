using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Market;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Services;

public sealed class AdvisorExecutionService(AccountDatabase database, ModelConnectionService connections, TradingCalendarRepository calendar, ComputeJobRepository jobs, ComputeService compute)
{
    private DateOnly lastCalendarRolloverCheck = DateOnly.MinValue;
    private static JsonObject Issue(string code, string message, string label, string url) => new()
    { ["code"] = code, ["message"] = message, ["symbol"] = null, ["label"] = label, ["url"] = url };

    public async Task<JsonObject> Preflight(string user, string kind, CancellationToken cancellation)
    {
        if (kind is not ("morning" or "afternoon")) throw new ArgumentException("报告类型无效");
        var config = connections.Config(user);
        var issues = new JsonArray();
        var input = database.Read(sql =>
        {
            if (!config["configured"]!.GetValue<bool>()) issues.Add(Issue("model_configuration", "模型或 Jev 服务尚未配置。", "配置模型", "#/pages/settings/index"));
            if (sql.Scalar("SELECT 1 FROM market_advisor_reports WHERE user_id=$1 AND status IN('queued','running') LIMIT 1", user) is not null)
                issues.Add(Issue("active_report", "已有报告正在执行，请完成后再生成。", "查看报告", kind == "morning" ? "#/pages/ai-stock/index" : "#/pages/fund-review/index"));
            if ((long)sql.Scalar("SELECT count(*) FROM market_advisor_reports WHERE user_id=$1 AND report_date=$2", user, FundFeeRepository.Today())! >= 4)
                issues.Add(Issue("quota_exceeded", "今日报告额度已用完。", "查看历史", "#/pages/fund-review/index"));
            if (ModelCostRepository.UnboundedBudgetEnabled(sql, user))
                issues.Add(Issue("unbounded_model_cost", "已启用金额预算，但当前连接没有可靠调用费用上限，不能调用模型。", "检查费用设置", "#/pages/settings/index"));
            return new JsonObject { ["report_kind"] = kind,
                ["snapshot"] = AnalysisSnapshotRepository.Create(sql, user),
                ["strategy"] = Document(sql, user, "strategy-settings", new() { ["parameters"] = new JsonObject() }),
                ["preferences"] = Document(sql, user, "preferences", new() { ["watchlist"] = new JsonArray() }) };
        });
        var canGenerate = issues.Count == 0;
        JsonObject result = new() { ["decision_ready"] = false, ["source_count"] = 0, ["symbols"] = new JsonArray() };
        if (canGenerate) result = (await compute.Run(user, "advisor.preflight", input, cancellation, seconds: 95))!.AsObject();
        if (result["issues"] is JsonArray evidence) foreach (var issue in evidence) issues.Add(issue?.DeepClone());
        result["issues"] = issues;
        result["kind"] = kind;
        result["can_generate"] = canGenerate;
        result["model_calls"] = 0;
        result["models"] = config["models"]?.DeepClone();
        result["checked_at"] = DateTimeOffset.UtcNow.ToString("O");
        result["expires_at"] = DateTimeOffset.UtcNow.AddMinutes(2).ToString("O");
        result["research_only"] = true;
        return result;
    }
    public static JsonObject Document(AccountSql sql,string user,string key,JsonObject fallback) => sql.Scalar("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key=$2",user,key) is string raw ? JsonNode.Parse(raw)!.AsObject() : fallback;
    private static JsonObject View(JsonObject row)
    {
        row["report"] = row["report_json"]?.DeepClone();row.Remove("report_json");
        row["progress"] = row["progress_json"]?.DeepClone();row.Remove("progress_json");
        row["models"] = row["model_config_json"]?.DeepClone();row.Remove("model_config_json");
        var expired = row["report"]?["expires_at"] is JsonValue expiry && DateTimeOffset.TryParse(expiry.GetValue<string>(),out var date) && date<=DateTimeOffset.UtcNow;
        if(expired && row["report"]?["decisions"] is JsonArray decisions) foreach(var decision in decisions.OfType<JsonObject>()) {decision["action"]="watch";decision["blockers"]!.AsArray().Add("报告已过期，当前只能查阅历史观点");}
        row["expired"]=expired;row["research_only"]=true;row["automatic_trading"]=false;row.Remove("user_id");row.Remove("input_json");
        return row;
    }
    public JsonObject Get(string user,string id) => database.Read(sql=>sql.Query("SELECT * FROM market_advisor_reports WHERE user_id=$1 AND report_id=$2",user,id).FirstOrDefault() is JsonObject row ? View(row) : throw new ApplicationFailure("report_not_found",404,"未找到AI报告。"));
    public JsonObject List(string user,string? kind,DateOnly? date,int page,int limit)
    {
        if(kind is not(null or "morning" or "afternoon") || page is <1 or >100000 || limit is <1 or >100) throw new ArgumentException("报告查询条件无效");
        var config=connections.Config(user);
        return database.Read(sql=>
        {
            var rows=sql.Query("SELECT * FROM market_advisor_reports WHERE user_id=$1 AND ($2::text IS NULL OR kind=$2) AND ($3::date IS NULL OR report_date=$3) ORDER BY created_at DESC,report_id DESC LIMIT $4 OFFSET $5",user,kind,date,limit,(page-1)*limit);
            var used=Convert.ToInt32(sql.Scalar("SELECT count(*) FROM market_advisor_reports WHERE user_id=$1 AND report_date=$2",user,FundFeeRepository.Today()));
            return new JsonObject { ["items"]=new JsonArray(rows.OfType<JsonObject>().Select(r=>(JsonNode?)View(r.DeepClone().AsObject())).ToArray()),["config"]=config,
                ["total"]=Convert.ToInt64(sql.Scalar("SELECT count(*) FROM market_advisor_reports WHERE user_id=$1 AND ($2::text IS NULL OR kind=$2) AND ($3::date IS NULL OR report_date=$3)",user,kind,date)),["page"]=page,["limit"]=limit,
                ["active_reports"]=sql.Query("SELECT report_id,kind,status FROM market_advisor_reports WHERE user_id=$1 AND status IN('queued','running') ORDER BY created_at DESC",user),
                ["usage"]=new JsonObject { ["date"]=FundFeeRepository.Today().ToString("yyyy-MM-dd"),["used"]=used,["limit"]=4,["remaining"]=Math.Max(0,4-used) } };
        });
    }
    public JsonObject Create(string user,string kind,bool force=false)
    {
        if(kind is not("morning" or "afternoon")) throw new ArgumentException("报告类型无效");
        var id=database.WriteNonLedger(user,sql=>
        {
            var config=connections.Config(user);
            if(!config["configured"]!.GetValue<bool>()) throw new ApplicationFailure("model_configuration",422,"模型或Jev服务尚未配置。");
            var old=sql.Query("SELECT report_id,status FROM market_advisor_reports WHERE user_id=$1 AND report_date=$2 AND kind=$3 ORDER BY created_at DESC LIMIT 1",user,FundFeeRepository.Today(),kind).FirstOrDefault();
            if(old is not null && (!force || old["status"]!.GetValue<string>() is "queued" or "running")) return old["report_id"]!.GetValue<string>();
            if(sql.Scalar("SELECT 1 FROM market_advisor_reports WHERE user_id=$1 AND status IN('queued','running') LIMIT 1",user) is not null) throw new AdvisorBusyError();
            if (ModelCostRepository.UnboundedBudgetEnabled(sql, user)) throw new ApplicationFailure("model_budget_exceeded", 409, "当前连接没有可靠调用费用上限，金额预算已阻止模型调用，未消耗报告额度。");
            if((long)sql.Scalar("SELECT count(*) FROM market_advisor_reports WHERE user_id=$1 AND report_date=$2",user,FundFeeRepository.Today())!>=4) throw new ApplicationFailure("quota_exceeded",429,"今日AI报告额度已用完，失败和重新生成也计入额度。");
            var reportId=Guid.NewGuid().ToString("N");
            var models=config["models"]!;
            var input=new JsonObject { ["public_id"]=reportId,["report_kind"]=kind,["models"]=models.DeepClone(),["snapshot"]=AnalysisSnapshotRepository.Create(sql,user),
                ["strategy"]=Document(sql,user,"strategy-settings",new(){["parameters"]=new JsonObject()}),["preferences"]=Document(sql,user,"preferences",new(){["watchlist"]=new JsonArray()}) };
            sql.Execute("INSERT INTO market_advisor_reports(user_id,report_id,report_date,kind,status,created_at,updated_at,model_config_json) VALUES($1,$2,$3,$4,'queued',now(),now(),$5::json)",user,reportId,FundFeeRepository.Today(),kind,models.ToJsonString());
            ComputeJobRepository.EnqueueOwned(sql,user,"advisor.report",input.ToJsonString(),DateTime.UtcNow.AddMinutes(10));
            return reportId;
        });
        return Get(user,id);
    }
    public JsonObject Schedule(string user,bool running) => database.Read(sql=>
    {
        var today=FundFeeRepository.Today();var ready=calendar.Load(today.Year) is not null;
        return new JsonObject { ["owner"]="system",["running"]=running,["timezone"]="Asia/Shanghai",["schedules"]=new JsonObject { ["morning"]="09:30",["afternoon"]="14:30" },
            ["trading_day"]=calendar.TradingDay(today),["calendar_year"]=today.Year,["calendar_status"]=ready?"ready":"missing",["calendar_warning"]=ready?null:$"{today.Year}年交易日历缺失，请管理者导入交易所休市公告，自动报告暂暂停。",
            ["items"]=sql.Query("SELECT run_date,kind,status,started_at,report_id,error_code FROM advisor_schedule_runs WHERE user_id=$1 ORDER BY started_at DESC LIMIT 8",user),["automatic_trading"]=false };
    });
    public void Tick()
    {
        jobs.Recover();
        CheckCalendarRollover();
        database.WriteNonLedger("compute:reaper",sql=>sql.Execute("UPDATE advisor_schedule_runs SET status='interrupted',error_code='schedule_interrupted' WHERE status='claimed' AND report_id IS NULL AND started_at<now()-interval '2 minutes'"));
        var now=DateTime.UtcNow.AddHours(8);var date=DateOnly.FromDateTime(now);
        if(calendar.TradingDay(date)!=true) return;
        var kind=now.Hour==9 && now.Minute is >=30 and <45 ? "morning" : now.Hour==14 && now.Minute is >=30 and <45 ? "afternoon" : null;
        if(kind is null) return;
        var users=database.Read(sql=>sql.Query("SELECT id FROM users WHERE status='active' ORDER BY id"));
        foreach(var user in users.OfType<JsonObject>())
        {
            var id=user["id"]!.GetValue<string>();
            var claimed=database.WriteNonLedger(id,sql=>sql.Execute("INSERT INTO advisor_schedule_runs(user_id,run_date,kind,status,started_at) VALUES($1,$2,$3,'claimed',now()) ON CONFLICT(user_id,run_date,kind) DO NOTHING",id,date,kind));
            if(claimed==0) continue;
            try
            {
                var report=Create(id,kind);
                database.WriteNonLedger(id,sql=>sql.Execute("UPDATE advisor_schedule_runs SET report_id=$4,status=$5,error_code=$6 WHERE user_id=$1 AND run_date=$2 AND kind=$3 AND status='claimed'",id,date,kind,report["report_id"]!.GetValue<string>(),report["status"]!.GetValue<string>(),report["error_code"]?.GetValue<string>()));
            }
            catch(Exception error) when(error is BusinessError or ArgumentException)
            { database.WriteNonLedger(id,sql=>sql.Execute("UPDATE advisor_schedule_runs SET status='failed',error_code='schedule_failed' WHERE user_id=$1 AND run_date=$2 AND kind=$3 AND status='claimed'",id,date,kind)); }
        }
    }

    /// <summary>E5：顾问通知列表（原 AdvisorController 内嵌 SQL，语义一致）。</summary>
    public async Task<JsonArray> NotificationsAsync(string userId, CancellationToken cancellation = default) =>
        await database.ReadAsync(async sql => calendar.TradingDay(FundFeeRepository.Today()) == true
            ? await sql.QueryAsync("SELECT id,kind,report_id,status,error_code,created_at,acknowledged FROM advisor_notifications WHERE user_id=$1 AND NOT acknowledged AND (created_at AT TIME ZONE 'Asia/Shanghai')::date=$2 ORDER BY created_at DESC LIMIT 10", userId, FundFeeRepository.Today())
            : new JsonArray(), cancellation);

    /// <summary>E5：确认顾问通知（原 AdvisorController 内嵌 SQL，语义一致）。返回是否找到。</summary>
    public async Task<bool> AcknowledgeNotificationAsync(string userId, string noticeId, CancellationToken cancellation = default) =>
        await database.WriteNonLedgerAsync(userId,
            sql => sql.ExecuteAsync("UPDATE advisor_notifications SET acknowledged=true WHERE user_id=$1 AND id=$2", userId, noticeId), cancellation) > 0;

    /// <summary>
    /// F1：每日日历 rollover 检查。10–12 月（site_settings.calendar_policy 的 months，
    /// 无则默认 10,11,12）若缺次年日历→调 worker op 生成→SaveAuto（待确认）→admin_audit 记录。
    /// 失败不中断 Tick，次日重试。
    /// </summary>
    public void CheckCalendarRollover()
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        if (lastCalendarRolloverCheck == today) return;
        lastCalendarRolloverCheck = today;
        var months = new[] { 10, 11, 12 };
        try
        {
            // calendar_policy 由迁移 0028 写入：{"policy_version":1,"auto_accept":true,"rollover_months":[10,11,12],"confirmed_years":[]}。
            if (database.Read(sql => sql.Scalar("SELECT value FROM site_settings WHERE key='calendar_policy'")) as string is { } raw
                && JsonNode.Parse(raw)?["rollover_months"]?.AsArray() is { Count: > 0 } array)
                months = array.Select(m => m!.GetValue<int>()).Where(m => m is >= 1 and <= 12).ToArray();
        }
        catch (Exception) { /* 用默认月份 */ }
        if (!months.Contains(today.Month)) return;
        var nextYear = today.Year + 1;
        if (nextYear is < 2000 or > 2100) return;
        foreach (var market in MarketCalendars.Markets)
        {
            try
            {
                // 完全缺失才生成；已有（含未确认的自动日历）不重复生成。
                if (calendar.View(nextYear, market)["configured"]!.GetValue<bool>()) continue;
                // 费用归属最早创建的活跃管理员（无则最早活跃用户）；无用户则跳过。
                var userId = database.Read(sql => sql.Query("SELECT id FROM users WHERE status='active' ORDER BY (role='admin') DESC, created_at LIMIT 1"))
                    .FirstOrDefault()?["id"]?.GetValue<string>();
                if (userId is null) continue;
                var result = compute.Run(userId, "calendar.generate_next_year", new { market, year = nextYear }, CancellationToken.None, seconds: 300)
                    .GetAwaiter().GetResult()?.AsObject();
                if (result is null || result["status"]?.GetValue<string>() is string status && status != "ok" || result["error_code"] is not null) continue;
                var holidays = result["holidays"]?.AsArray().Select(h => h!.GetValue<string>()).ToArray();
                if (holidays is null) continue;
                calendar.SaveAuto(userId, market, nextYear, holidays,
                    result["source"]?.GetValue<string>() ?? "worker:calendar.generate_next_year",
                    result["confidence"]?.GetValue<double>(),
                    result["notes"]?.AsArray().Select(n => n!.GetValue<string>()).ToArray(),
                    new JsonObject { ["op"] = "calendar.generate_next_year", ["at"] = DateTimeOffset.UtcNow.ToString("O"), ["trigger"] = "scheduler-rollover" });
            }
            catch (Exception) { /* rollover 失败不中断 Tick，次日重试 */ }
        }
    }

    /// <summary>
    /// F9：管理者手动重跑定时报告。定时窗口（北京时间 09:30–09:44 / 14:30–14:44）外
    /// 需要 confirm=true 二次确认，否则 409。重跑生成当日新报告并回写 advisor_schedule_runs。
    /// </summary>
    public JsonObject RerunSchedule(string userId, DateOnly runDate, string kind, bool confirm)
    {
        if (kind is not ("morning" or "afternoon")) throw new ArgumentException("报告类型无效");
        var now = DateTime.UtcNow.AddHours(8);
        var inWindow = now.Hour == 9 && now.Minute is >= 30 and < 45 || now.Hour == 14 && now.Minute is >= 30 and < 45;
        if (!inWindow && !confirm)
            throw new ApplicationFailure("rerun_confirm_required", 409, "当前不在定时窗口（09:30–09:44 / 14:30–14:44，北京时间）内，手动重跑需要二次确认（confirm=true）。");
        if (database.Read(sql => sql.Scalar("SELECT 1 FROM users WHERE id=$1 AND status='active'", userId)) is null)
            throw new ApplicationFailure("user_not_found", 404, "用户不存在或已停用。");
        var report = Create(userId, kind, force: true);
        database.WriteNonLedger(userId, sql =>
        {
            var changed = sql.Execute("UPDATE advisor_schedule_runs SET report_id=$4,status=$5,error_code=$6,started_at=now() WHERE user_id=$1 AND run_date=$2 AND kind=$3",
                userId, runDate, kind, report["report_id"]!.GetValue<string>(), report["status"]!.GetValue<string>(), report["error_code"]?.GetValue<string>());
            if (changed == 0)
                sql.Execute("INSERT INTO advisor_schedule_runs(user_id,run_date,kind,status,started_at,report_id,error_code) VALUES($1,$2,$3,$4,now(),$5,$6)",
                    userId, runDate, kind, report["status"]!.GetValue<string>(), report["report_id"]!.GetValue<string>(), report["error_code"]?.GetValue<string>());
            return 0;
        });
        return report;
    }
}

public sealed class NativeScheduler(AdvisorExecutionService service,IConfiguration configuration,ILogger<NativeScheduler> logger):BackgroundService
{
    public bool Running {get;private set;}
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if(configuration["QUANT_DISABLE_SCHEDULER"]=="true") return;
        Running=true;
        try
        {
            while(!stoppingToken.IsCancellationRequested)
            {
                try { service.Tick(); } catch(Exception error) { logger.LogWarning("Native scheduler tick failed: {FailureType}",error.GetType().Name); }
                await Task.Delay(TimeSpan.FromSeconds(10),stoppingToken);
            }
        }
        catch(OperationCanceledException) when(stoppingToken.IsCancellationRequested) { }
        finally { Running=false; }
    }
}
