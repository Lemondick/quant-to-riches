using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Auth;

public sealed class AdminDataRepository(AccountDatabase database, AdminAuditRepository auditChain)
{
    private const string Holdings = """
        SELECT p.*,coalesce(i.name,p.symbol) AS name,
            CASE WHEN p.asset_type<>'cash' AND p.average_cost>0 THEN (p.quantity*p.average_cost)::text ELSE NULL END AS cost_value,
            CASE WHEN p.asset_type<>'cash' AND p.average_cost>0 THEN (p.market_value-p.quantity*p.average_cost)::text ELSE NULL END AS floating_profit
        FROM (
            SELECT user_id,'local' AS source,'' AS account_id,'本地主账本' AS account_name,'local-ledger' AS platform,'CNY' AS currency,
                symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at FROM positions WHERE user_id=$1 AND quantity>0
            UNION ALL SELECT p.user_id,'snapshot',p.account_id,a.name,a.platform,a.currency,p.symbol,p.asset_type,p.quantity,p.average_cost,p.market_value,p.as_of_date,p.updated_at
                FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id WHERE p.user_id=$1 AND a.status='active' AND p.quantity>0
        ) p LEFT JOIN instruments i ON i.user_id=p.user_id AND i.symbol=p.symbol
        """;
    private const string Ledger = """
        SELECT t.id,CASE WHEN lower(coalesce(t.source,''))='dca' THEN 'dca' WHEN t.action IN('buy','add') THEN 'buy' WHEN t.action IN('sell','reduce') THEN 'sell' ELSE t.action END AS entry_type,
            t.symbol,coalesce(i.name,t.symbol) AS name,t.asset_type,t.quantity,t.price,t.gross_amount,t.fees,t.cash_delta AS net_amount,t.traded_at AS occurred_at,t.note,t.reversed_by_correction_id
            FROM transactions t LEFT JOIN instruments i ON i.user_id=t.user_id AND i.symbol=t.symbol WHERE t.user_id=$1
        UNION ALL SELECT d.id,'dividend',d.symbol,coalesce(i.name,d.symbol),d.asset_type,NULL,NULL,d.gross_amount,d.fees,d.net_amount,d.paid_at,d.note,d.reversed_by_correction_id
            FROM dividend_events d LEFT JOIN instruments i ON i.user_id=d.user_id AND i.symbol=d.symbol WHERE d.user_id=$1
        """;
    private const string Reports = """
        SELECT 'advisor' AS source,report_id,0 AS attempt_no,kind,'' AS symbol,status,report_date,created_at,updated_at,error_code,report_json IS NOT NULL AS has_content
            FROM market_advisor_reports WHERE user_id=$1
        UNION ALL SELECT 'research',a.job_id,a.attempt_no,'stock_research',j.symbol,a.execution_status,a.quota_date,a.started_at,a.completed_at,
            CASE WHEN j.attempt_no=a.attempt_no THEN j.error_code ELSE NULL END,true
            FROM agent_research_attempts a JOIN agent_research_jobs j ON j.user_id=a.user_id AND j.job_id=a.job_id WHERE a.user_id=$1 AND a.report_json IS NOT NULL
        """;
    private const string Tasks = "SELECT job_id,symbol,status,attempt_no,phase,error_code,created_at FROM agent_research_jobs WHERE user_id=$1";
    private const string BusinessEvents = """
        SELECT 'audit:'||id::text AS id,CASE WHEN entity_type='portfolio_account_import' THEN 'imports' WHEN entity_type IN('transaction','dividend','external_cash_flow','corporate_action','ledger_correction') THEN 'trades' ELSE 'portfolio' END AS category,
            entity_type AS action,entity_id AS reference,changed_at AS occurred_at,'completed' AS status FROM audit_log WHERE user_id=$1
        UNION ALL SELECT 'snapshot:'||id::text,CASE WHEN dataset='fund_nav' THEN 'nav' ELSE 'data' END,'data_ingested',dataset,fetched_at,status FROM data_snapshots WHERE user_id=$1
        UNION ALL SELECT 'advisor:'||report_id,'reports','advisor_report_outcome',kind,updated_at,status FROM market_advisor_reports WHERE user_id=$1 AND status IN('completed','blocked','failed')
        UNION ALL SELECT 'research:'||job_id||':'||attempt_no::text,'tasks','research_task_outcome',job_id,completed_at,execution_status FROM agent_research_attempts WHERE user_id=$1 AND completed_at IS NOT NULL AND execution_status NOT IN('queued','running','cancel_requested')
        """;
    private const string AccountEvents = """
        SELECT 'account:created' AS id,'account' AS category,'account_created' AS action,'' AS reference,created_at AS occurred_at,'completed' AS status FROM users WHERE id=$1
        UNION ALL SELECT 'account:'||id::text,'account',action,'',created_at,'completed' FROM account_activity WHERE user_id=$1
        UNION ALL SELECT 'account:last-login','account','last_login_recorded','',last_login_at,'completed' FROM users WHERE id=$1 AND last_login_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM account_activity WHERE user_id=$1 AND action='login' AND created_at=last_login_at)
        UNION ALL SELECT 'admin:'||id::text,'account',action,CASE WHEN action='status' THEN detail::json->>'value' ELSE '' END,created_at,'completed' FROM admin_audit WHERE target_id=$1 AND action IN('status','revoke_sessions','recovery_issued')
        """;

    private static JsonObject User(AccountSql sql, string userId) => sql.Query("SELECT id,username,role,status,created_at,last_login_at FROM users WHERE id=$1", userId).FirstOrDefault()?.DeepClone().AsObject() ?? throw new AuthFailure("account_not_found");
    private void Audit(AccountSql sql, string actor, string target, string action, string category, JsonObject range)
    {
        if ((long)sql.Scalar("SELECT count(*) FROM users WHERE id=$1 AND role='admin' AND status='active'", actor)! != 1) throw new AuthFailure("admin_required");
        auditChain.Write(sql, actor, action, target,
            new JsonObject { ["category"] = category, ["range"] = range }.ToJsonString(), DateTime.UtcNow);
    }

    public static JsonObject Summary(AccountSql sql, string userId) => sql.Query("""
        SELECT 'ready' AS data_status,(SELECT count(DISTINCT symbol) FROM
            (SELECT symbol FROM positions WHERE user_id=$1 AND asset_type<>'cash' AND quantity>0 UNION ALL SELECT p.symbol FROM account_positions p JOIN portfolio_accounts a ON a.user_id=p.user_id AND a.account_id=p.account_id WHERE p.user_id=$1 AND a.status='active' AND p.asset_type<>'cash' AND p.quantity>0) holdings) AS position_count,
            (SELECT count(*) FROM transactions WHERE user_id=$1)+(SELECT count(*) FROM dividend_events WHERE user_id=$1) AS ledger_count,
            (SELECT count(*) FROM market_advisor_reports WHERE user_id=$1)+(SELECT count(*) FROM agent_research_attempts WHERE user_id=$1 AND report_json IS NOT NULL) AS report_count
        """, userId)[0]!.DeepClone().AsObject();

    public JsonArray Users(string? search)
    {
        if(search?.Length>32)throw new ArgumentException("用户名查询过长");
        var selected=search?.Trim().ToLowerInvariant()??"";
        return database.Read(sql=>
        {
            var users=sql.Query("SELECT id,username,role,status,created_at,last_login_at FROM users WHERE $1='' OR strpos(username,$1)>0 ORDER BY created_at,id",selected);
            foreach(var user in users.OfType<JsonObject>())
            {
                var id=user["id"]!.GetValue<string>();
                foreach(var pair in Summary(sql,id))user[pair.Key]=pair.Value?.DeepClone();
                var usage=sql.Query("SELECT coalesce(sum(requests) FILTER(WHERE day>=((now() AT TIME ZONE 'Asia/Shanghai')::date-6)::text),0)::bigint AS requests_7d,coalesce(sum(errors) FILTER(WHERE day>=((now() AT TIME ZONE 'Asia/Shanghai')::date-6)::text),0)::bigint AS errors_7d,max(last_activity_at) AS last_activity_at FROM usage_daily WHERE user_id=$1",id)[0]!.AsObject();
                foreach(var pair in usage)user[pair.Key]=pair.Value?.DeepClone();
            }
            return users;
        });
    }

    public JsonObject Details(string actor, string userId, string section, int page, int limit, string symbol, DateOnly? start, DateOnly? end, bool export = false)
    {
        if (section is not ("holdings" or "ledger" or "watchlist" or "reports" or "tasks") || page is < 1 or > 100000 || limit is < 1 or > 100 || symbol.Length > 32 || end < start)
            throw new ArgumentException("账户查询参数无效");
        return database.WriteNonLedger(actor, sql =>
        {
            var user = User(sql, userId); var overview = Summary(sql, userId);
            var totals = sql.Query("SELECT coalesce(sum(market_value),0) AS local_market_value,coalesce(sum(market_value) FILTER(WHERE asset_type='cash'),0) AS local_cash FROM positions WHERE user_id=$1 AND quantity>0", userId)[0]!.AsObject();
            foreach (var pair in totals) overview[pair.Key] = pair.Value?.DeepClone();
            overview["accounts"] = sql.Query("""
                SELECT a.account_id,a.name,a.platform,a.currency,count(p.symbol) AS position_count,coalesce(sum(p.market_value),0) AS market_value,max(p.as_of_date) AS as_of_date
                FROM portfolio_accounts a LEFT JOIN account_positions p ON p.user_id=a.user_id AND p.account_id=a.account_id AND p.quantity>0 WHERE a.user_id=$1 AND a.status='active'
                GROUP BY a.account_id,a.name,a.platform,a.currency ORDER BY a.account_id
                """, userId);
            JsonArray items; long total;
            if (section == "watchlist")
            {
                var raw = sql.Scalar("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key='preferences'", userId) as string;
                var entries = (raw is null ? [] : JsonNode.Parse(raw)!["watchlist"]!.AsArray().OfType<JsonObject>()).Where(r => symbol.Length == 0 || r["symbol"]!.GetValue<string>().Contains(symbol, StringComparison.OrdinalIgnoreCase)).ToArray();
                total = entries.Length; items = new JsonArray(entries.Skip(export ? 0 : (page - 1) * limit).Take(export ? 100000 : limit).Select(r => r.DeepClone()).ToArray());
            }
            else
            {
                var (projection, order, time) = section switch
                { "holdings" => (Holdings, "source,account_id,asset_type,symbol", "as_of_date"), "ledger" => (Ledger, "occurred_at DESC,entry_type,id DESC", "(occurred_at AT TIME ZONE 'Asia/Shanghai')::date"), "reports" => (Reports, "created_at DESC,source,report_id,attempt_no DESC", "(created_at AT TIME ZONE 'Asia/Shanghai')::date"), _ => (Tasks, "created_at DESC,job_id", "(created_at AT TIME ZONE 'Asia/Shanghai')::date") };
                var filtered = $"SELECT * FROM ({projection}) rows WHERE ($2='' OR symbol ILIKE '%'||$2||'%') AND ($3::date IS NULL OR {time}>=$3) AND ($4::date IS NULL OR {time}<=$4)";
                total = (long)sql.Scalar($"SELECT count(*) FROM ({filtered}) rows", userId, symbol, start, end)!;
                if (export && total > 100000) throw new ApplicationFailure("history_export_too_large", 413, "单次导出最多10万条，请缩小日期范围。");
                items = sql.Query($"SELECT * FROM ({filtered}) rows ORDER BY {order} LIMIT $5 OFFSET $6", userId, symbol, start, end, export ? 100000 : limit, export ? 0 : (page - 1) * limit);
                foreach (var row in items.OfType<JsonObject>()) row.Remove("user_id");
            }
            Audit(sql, actor, userId, export ? "details_export" : "details_read", section, new() { ["symbol"] = symbol, ["start"] = start?.ToString("yyyy-MM-dd"), ["end"] = end?.ToString("yyyy-MM-dd"), ["page"] = page, ["limit"] = limit });
            return export ? new JsonObject { ["user_id"] = userId, ["items"] = items, ["total"] = total }
                : new JsonObject { ["user"] = user, ["data_status"] = "ready", ["overview"] = overview, ["section"] = section, ["page"] = page, ["limit"] = limit, ["total"] = total, ["items"] = items };
        });
    }

    public JsonObject Report(string actor, string userId, string source, string reportId, int attempt)
    {
        if (source is not ("advisor" or "research") || string.IsNullOrWhiteSpace(reportId) || reportId.Length > 128 || attempt is < 1 or > 100000) throw new ArgumentException("报告查询参数无效");
        return database.WriteNonLedger(actor, sql =>
        {
            User(sql, userId);
            var rows = source == "advisor" ? sql.Query("SELECT report_id,kind,status,report_date,created_at,updated_at,report_json AS report FROM market_advisor_reports WHERE user_id=$1 AND report_id=$2", userId, reportId)
                : sql.Query("SELECT a.job_id AS report_id,a.attempt_no,j.symbol,a.execution_status AS status,a.quota_date AS report_date,a.started_at AS created_at,a.completed_at AS updated_at,a.report_json AS report FROM agent_research_attempts a JOIN agent_research_jobs j ON j.user_id=a.user_id AND j.job_id=a.job_id WHERE a.user_id=$1 AND a.job_id=$2 AND a.attempt_no=$3 AND a.report_json IS NOT NULL", userId, reportId, attempt);
            var row = rows.FirstOrDefault()?.AsObject() ?? throw new AuthFailure("report_not_found");
            if (row["report"] is not (null or JsonObject)) throw new AuthFailure("user_data_unavailable");
            row["user_id"] = userId; row["source"] = source; row["historical_record"] = true;
            Audit(sql, actor, userId, "report_read", "reports", new() { ["source"] = source, ["report_id"] = reportId, ["attempt_no"] = attempt }); return row;
        });
    }

    public JsonObject Usage(string actor, string userId) => database.WriteNonLedger(actor, sql =>
    {
        User(sql, userId);
        var rows = sql.Query("SELECT day,category,requests,errors,duration_ms FROM usage_daily WHERE user_id=$1 AND day>=$2 ORDER BY day DESC,category", userId, DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)).AddDays(-6).ToString("yyyy-MM-dd"));
        var tasks = sql.Query("""
            SELECT * FROM (SELECT 'advisor' AS source,report_id,status,error_code,created_at AS started_at,updated_at AS completed_at FROM market_advisor_reports WHERE user_id=$1
            UNION ALL SELECT 'research',a.job_id,a.execution_status,CASE WHEN a.attempt_no=j.attempt_no THEN j.error_code ELSE NULL END,a.started_at,a.completed_at
            FROM agent_research_attempts a JOIN agent_research_jobs j ON j.user_id=a.user_id AND j.job_id=a.job_id WHERE a.user_id=$1) tasks
            WHERE started_at>=now()-interval '7 days' ORDER BY started_at DESC
            """, userId);
        var completed = tasks.OfType<JsonObject>().Where(r => r["status"]!.GetValue<string>() is not ("queued" or "running" or "cancel_requested") && r["completed_at"] is not null).ToArray();
        var errors = completed.Where(r => r["status"]!.GetValue<string>() is not ("succeeded" or "completed")).Take(10).ToArray();
        var last = sql.Scalar("SELECT max(last_activity_at) FROM usage_daily WHERE user_id=$1", userId);
        Audit(sql, actor, userId, "usage_read", "usage", new());
        return new JsonObject { ["last_activity_at"] = last is DateTime time ? new DateTimeOffset(time).ToOffset(TimeSpan.FromHours(8)).ToString("O") : null,
            ["requests_7d"] = rows.OfType<JsonObject>().Sum(r => r["requests"]!.GetValue<int>()), ["errors_7d"] = rows.OfType<JsonObject>().Sum(r => r["errors"]!.GetValue<int>()), ["items"] = rows,
            ["reports_7d"] = tasks.Count, ["reports_succeeded_7d"] = tasks.OfType<JsonObject>().Count(r => r["status"]!.GetValue<string>() is "succeeded" or "completed"),
            ["reports_failed_7d"] = completed.Count(r => r["status"]!.GetValue<string>() is not ("succeeded" or "completed")),
            ["average_duration_seconds"] = completed.Length == 0 ? null : Math.Round(completed.Average(r => Math.Max(0, (DateTimeOffset.Parse(r["completed_at"]!.GetValue<string>(), CultureInfo.InvariantCulture) - DateTimeOffset.Parse(r["started_at"]!.GetValue<string>(), CultureInfo.InvariantCulture)).TotalSeconds)), 2),
            ["recent_errors"] = new JsonArray(errors.Select(r => r.DeepClone()).ToArray()) };
    });

    public JsonObject Timeline(string actor, string userId, string category, int page, int limit, DateOnly? start, DateOnly? end)
    {
        if (category is not ("all" or "business" or "account" or "imports" or "trades" or "nav" or "reports" or "tasks" or "portfolio" or "data") || page is < 1 or > 100000 || limit is < 1 or > 100 || end < start) throw new ArgumentException("事件查询参数无效");
        return database.WriteNonLedger(actor, sql =>
        {
            var user = User(sql, userId); var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)); var first = today.AddDays(-29);
            var projection = category == "account" ? AccountEvents : category == "all" ? BusinessEvents + " UNION ALL " + AccountEvents : BusinessEvents;
            var filtered = $"SELECT * FROM ({projection}) events WHERE ($2 IN('all','business','account') OR category=$2) AND ($3::date IS NULL OR (occurred_at AT TIME ZONE 'Asia/Shanghai')::date>=$3) AND ($4::date IS NULL OR (occurred_at AT TIME ZONE 'Asia/Shanghai')::date<=$4)";
            var total = (long)sql.Scalar($"SELECT count(*) FROM ({filtered}) events", userId, category, start, end)!;
            var items = sql.Query($"SELECT * FROM ({filtered}) events ORDER BY occurred_at DESC,id DESC LIMIT $5 OFFSET $6", userId, category, start, end, limit, (page - 1) * limit);
            var daily = sql.Query($"SELECT (occurred_at AT TIME ZONE 'Asia/Shanghai')::date AS day,count(*) AS actions,count(*) FILTER(WHERE status IN('failed','blocked','timed_out','cancelled','interrupted')) AS failures FROM ({BusinessEvents}) events WHERE (occurred_at AT TIME ZONE 'Asia/Shanghai')::date BETWEEN $2 AND $3 GROUP BY day", userId, first, today).OfType<JsonObject>().ToDictionary(r => r["day"]!.GetValue<string>());
            var trends = new JsonArray();
            for (var date = first; date <= today; date = date.AddDays(1)) { var key = date.ToString("yyyy-MM-dd"); trends.Add(daily.TryGetValue(key, out var row) ? row.DeepClone() : new JsonObject { ["day"] = key, ["actions"] = 0, ["failures"] = 0 }); }
            Audit(sql, actor, userId, "timeline_read", category, new() { ["start"] = start?.ToString("yyyy-MM-dd"), ["end"] = end?.ToString("yyyy-MM-dd"), ["page"] = page, ["limit"] = limit });
            return new JsonObject { ["user"] = user, ["page"] = page, ["limit"] = limit, ["total"] = total, ["items"] = items, ["trends"] = trends,
                ["trend_scope"] = "business_actions", ["request_polling_included"] = false, ["history_note"] = "仅展示已保存的业务事件与账号记录；缺失的旧历史不会补写。" };
        });
    }
}
