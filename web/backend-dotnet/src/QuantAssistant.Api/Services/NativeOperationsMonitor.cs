using QuantAssistant.Core.Abstractions;
using System.Globalization;
using System.Text.Json.Nodes;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Services;

/// <summary>Administrator operational evidence; does not start jobs or perform recovery.</summary>
public sealed class NativeOperationsMonitor(IConfiguration configuration, TradingCalendarRepository calendars, NativeMetrics metrics, TimeProvider clock)
{
    private int Setting(string key, int fallback, int maximum) => int.TryParse(configuration[key], out var value) && value > 0 && value <= maximum ? value : fallback;

    public JsonObject Workers(IAccountSql sql)
    {
        var active = Convert.ToInt64(sql.Scalar("SELECT count(*) FROM worker_heartbeats WHERE last_seen_at>now()-interval '30 seconds' AND NOT stopping"));
        var expected = Setting("QUANT_EXPECTED_WORKERS", 1, 3);
        return new JsonObject { ["active"] = active, ["expected"] = expected, ["status"] = active >= expected ? "healthy" : "degraded" };
    }

    public JsonObject Read(IAccountSql sql, JsonArray archives)
    {
        var now = clock.GetUtcNow();
        var alerts = new JsonArray();
        void Alert(string code, string message, string severity = "warning") => alerts.Add(new JsonObject { ["code"] = code, ["message"] = message, ["severity"] = severity });
        var workers = Workers(sql);
        if (workers["status"]!.GetValue<string>() != "healthy") Alert("workers_missing", $"计算执行器 {workers["active"]} / {workers["expected"]}，请检查进程和心跳。", "critical");
        var queue = NativeRuntimeMaintenance.Queue(sql);
        var database = NativeRuntimeMaintenance.Database(sql);
        if (database["blocked_connections"]!.GetValue<long>() > 0) Alert("database_lock_wait", "数据库存在锁等待，请检查长事务。");
        if (database["all_session_statistics"]?.GetValue<bool>() != true) Alert("database_statistics_limited", "数据库活动统计权限不足，最长查询与空闲事务无法完整确认。");
        if (database["longest_query_seconds"]?.GetValue<double>() > 30) Alert("database_slow_query", "数据库存在超过30秒的查询，请检查运行任务。");
        if (queue["oldest_queued_seconds"]!.GetValue<long>() >= Setting("QUANT_QUEUE_WARNING_SECONDS", 120, 86400)) Alert("queue_waiting", "任务等待时间超过阈值，请核对执行器容量与任务状态。");
        if (queue["overdue"]!.GetValue<long>() > 0) Alert("task_overdue", "存在超过执行期限的任务，请检查回收器和执行器。", "critical");
        var disks = new JsonArray();
        foreach (var (label, path) in new[] { ("站点存储", configuration["QUANT_SITE_ROOT"] ?? AppContext.BaseDirectory), ("备份存储", configuration["QUANT_BACKUP_DIR"]) })
        {
            if (string.IsNullOrWhiteSpace(path)) continue;
            var disk = new JsonObject { ["label"] = label, ["status"] = "unknown" };
            try
            {
                var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(path))!);
                if (!drive.IsReady || drive.TotalSize <= 0) throw new IOException();
                var percent = 100.0 * drive.AvailableFreeSpace / drive.TotalSize;
                var low = percent < Setting("QUANT_DISK_MIN_FREE_PERCENT", 10, 100);
                disk["free_bytes"] = drive.AvailableFreeSpace; disk["total_bytes"] = drive.TotalSize; disk["free_percent"] = percent;
                disk["status"] = low ? "low" : "healthy";
                if (low) Alert("disk_low", label + "可用空间低于阈值，请检查归档和日志保留。", "critical");
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
            { Alert("disk_unknown", label + "空间暂不可读取，请检查路径及权限。"); }
            disks.Add(disk);
        }
        var configured = !string.IsNullOrWhiteSpace(configuration["QUANT_BACKUP_DIR"]);
        var latest = archives.OfType<JsonObject>().Where(item => item["verified"]?.GetValue<bool>() == true)
            .Select(item => DateTimeOffset.TryParse(item["created_at"]?.GetValue<string>() ?? item["modified_at"]?.GetValue<string>(), CultureInfo.InvariantCulture, DateTimeStyles.None, out var created) ? created : (DateTimeOffset?)null)
            .Where(created => created <= now).Max();
        var maxAge = Setting("QUANT_BACKUP_MAX_AGE_HOURS", 36, 8760);
        var age = latest is null ? (double?)null : (now - latest.Value).TotalHours;
        var backup = new JsonObject { ["configured"] = configured, ["last_verified_at"] = latest?.ToString("O"), ["age_hours"] = age, ["max_age_hours"] = maxAge };
        if (!configured) Alert("backup_not_configured", "尚未配置完整站点备份目录。");
        else if (latest is null) Alert("backup_missing", "没有通过校验的完整站点备份。", "critical");
        else if (age > maxAge) Alert("backup_stale", "最近有效备份已超过设定时限，请检查自动备份。", "critical");
        if (archives.OfType<JsonObject>().Any(item => item["verified"]?.GetValue<bool>() != true)) Alert("backup_invalid", "发现校验失败的归档，请检查备份存储。", "critical");
        backup["automation"] = ReadBackupState();
        if (backup["automation"]?["status"]?.GetValue<string>() is "failed" or "restart_failed") Alert("backup_automation_failed", "最近自动备份或服务恢复失败，请检查运维日志。", "critical");
        var calendar = calendars.Status(DateOnly.FromDateTime(now.UtcDateTime.AddHours(8)));
        foreach (var warning in calendar["warnings"]!.AsArray()) Alert("calendar_attention", warning!.GetValue<string>());
        // F1：自动生成待确认的日历告警（确认前跨年自动报告可能暂停）。
        var nextYear = DateOnly.FromDateTime(now.UtcDateTime.AddHours(8)).Year + 1;
        foreach (var market in QuantAssistant.Core.Market.MarketCalendars.Markets)
        {
            var view = calendars.View(nextYear, market);
            if (view["configured"]!.GetValue<bool>() && view["origin"]?.GetValue<string>() == "auto" && view["confirmed"]?.GetValue<bool>() == false)
                Alert("calendar_auto_pending", $"{market} {nextYear}年交易日历已自动生成待确认，请管理员核对后确认。");
        }
        // F3：公开源连续失败的 critical 告警（operational_alerts，跨用户去重展示）。
        var sourceAlerts = sql.Query("SELECT DISTINCT ON(alert_key) alert_key,severity,message,first_seen_at,details_json FROM operational_alerts WHERE status='open' AND alert_key LIKE 'public_source:%' ORDER BY alert_key,first_seen_at DESC LIMIT 20");
        foreach (var row in sourceAlerts.OfType<JsonObject>())
            Alert("public_source_failed", row["message"]!.GetValue<string>(), "critical");
        return new JsonObject { ["checked_at"] = now.ToString("O"), ["status"] = alerts.Count == 0 ? "healthy" : "degraded",
            ["workers"] = workers, ["queue"] = queue, ["disks"] = disks, ["backup"] = backup, ["calendar"] = calendar,
            ["metrics"] = metrics.Snapshot(), ["database"] = database, ["public_source_alerts"] = sourceAlerts,
            ["recent_failures"] = sql.Query("SELECT kind,status,error_code,completed_at FROM compute_jobs WHERE status IN('failed','interrupted') ORDER BY completed_at DESC NULLS LAST LIMIT 20"),
            ["trend"] = sql.Query("SELECT captured_at,payload FROM (SELECT captured_at,payload FROM native_monitor_samples ORDER BY captured_at DESC LIMIT 120) recent ORDER BY captured_at"),
            ["maintenance"] = sql.Query("SELECT * FROM native_maintenance_state WHERE id=1").FirstOrDefault()?.DeepClone(), ["alerts"] = alerts };
    }

    private JsonNode? ReadBackupState()
    {
        var directory = configuration["QUANT_BACKUP_DIR"];
        if (string.IsNullOrWhiteSpace(directory)) return null;
        try
        {
            var path = Path.Combine(directory, ".native-backup-status.json");
            if (!File.Exists(path)) return null;
            if (new FileInfo(path).Length > 16384) return new JsonObject { ["status"] = "failed" };
            var state = JsonNode.Parse(File.ReadAllText(path))?.AsObject() ?? throw new InvalidDataException();
            var result = new JsonObject();
            foreach (var key in new[] { "status", "attempted_at", "last_success_at", "error_code", "archive" })
            {
                if (state[key] is not null && state[key]!.GetValueKind() != System.Text.Json.JsonValueKind.String) throw new InvalidDataException();
                result[key] = state[key]?.DeepClone();
            }
            return result;
        }
        catch (Exception error) when (error is IOException or InvalidDataException or UnauthorizedAccessException or System.Text.Json.JsonException or InvalidOperationException)
        { return new JsonObject { ["status"] = "failed" }; }
    }
}
