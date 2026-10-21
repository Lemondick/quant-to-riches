using System.Globalization;

namespace QuantAssistant.Api.Configuration;

/// <summary>
/// E7：集中配置（Options 模式）。
/// 所有 QUANT_* 环境变量 / Configuration 键的收敛点：键名拼错在启动绑定时即暴露，
/// 不再以魔法字符串散落在各处。关键配置缺失时启动直接抛错退出（fail-fast）。
/// </summary>
public sealed class QuantOptions
{
    /// <summary>PostgreSQL 连接串（必填）：ConnectionStrings:Default 或 QUANT_PG_CONNECTION。</summary>
    public string ConnectionString { get; init; } = "";

    /// <summary>站点域名（QUANT_DOMAIN），用于 Origin 校验与安全检查。</summary>
    public string? Domain { get; init; }

    /// <summary>站点数据根目录（QUANT_SITE_ROOT）。</summary>
    public string? SiteRoot { get; init; }

    /// <summary>日志目录（QUANT_LOG_DIR）。</summary>
    public string? LogDir { get; init; }

    /// <summary>密钥根目录（QUANT_SECRET_ROOT）。</summary>
    public string? SecretRoot { get; init; }

    /// <summary>备份目录（QUANT_BACKUP_DIR）。</summary>
    public string? BackupDir { get; init; }

    /// <summary>前端静态资源根（QUANT_WEB_ROOT）。</summary>
    public string? WebRoot { get; init; }

    /// <summary>F4：模型预算预警比例（QUANT_MODEL_BUDGET_WARN_RATIO），默认 0.8。</summary>
    public double ModelBudgetWarnRatio { get; init; } = 0.8;

    /// <summary>F3：公开源连续失败 N 轮后向管理者发 critical 告警（QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS），默认 3。</summary>
    public int PublicSourceFailAlertRounds { get; init; } = 3;

    /// <summary>F1：日历政策（QUANT_CALENDAR_AUTO_ACCEPT），true=TradingDay 采纳未确认的自动日历，默认 true。</summary>
    public bool CalendarAutoAccept { get; init; } = true;

    /// <summary>batch2-2：估值百分位缓存分钟数（QUANT_VALUATION_CACHE_TTL_MIN），默认 360。</summary>
    public int ValuationCacheTtlMinutes { get; init; } = 360;

    /// <summary>batch2-3/4：赎回降档提前提醒天数（QUANT_REDEMPTION_REMIND_DAYS），默认 14。</summary>
    public int RedemptionRemindDays { get; init; } = 14;

    /// <summary>batch2-3/4：基金体检 cron（QUANT_HEALTH_CHECK_CRON），默认周一 06:00（北京时间）。Tick 内解析。</summary>
    public string HealthCheckCron { get; init; } = "0 6 * * 1";

    /// <summary>batch2-11：2FA 展示名（QUANT_TOTP_ISSUER），默认"观澜"。</summary>
    public string TotpIssuer { get; init; } = "观澜";

    /// <summary>batch2-12：审计哈希链算法（QUANT_AUDIT_HASH_ALGO），默认 SHA256。</summary>
    public string AuditHashAlgo { get; init; } = "SHA256";

    /// <summary>任务9：分钟/tick 级快照分区保留天数（QUANT_SNAPSHOT_RETENTION_TICK_DAYS），默认 90。
    /// 由 quant-snapshot-cleanup timer 经 GUC 传入 quant_snapshot_maintenance()；日线及以上快照永久保留。</summary>
    public int SnapshotRetentionTickDays { get; init; } = 90;

    /// <summary>batch3 流 A：风险指标缓存分钟数（QUANT_RISK_METRICS_CACHE_TTL_MIN），默认 60。</summary>
    public int RiskMetricsCacheTtlMinutes { get; init; } = 60;

    /// <summary>batch3 流 A：SMTP 服务器（QUANT_SMTP_HOST；site_settings 同名键可覆盖）。</summary>
    public string? SmtpHost { get; init; }

    /// <summary>batch3 流 A：SMTP 端口（QUANT_SMTP_PORT；site_settings 同名键可覆盖，未配默认 25）。</summary>
    public int? SmtpPort { get; init; }

    /// <summary>batch3 流 A：SMTP 用户名（QUANT_SMTP_USER；site_settings 同名键可覆盖）。</summary>
    public string? SmtpUser { get; init; }

    /// <summary>batch3 流 A：SMTP 密码（QUANT_SMTP_PASS；site_settings 同名键可覆盖。仅内存使用，永不落日志/响应）。</summary>
    public string? SmtpPass { get; init; }

    /// <summary>batch3 流 A：发件人地址（QUANT_SMTP_FROM；site_settings 同名键可覆盖）。</summary>
    public string? SmtpFrom { get; init; }

    /// <summary>batch3 流 A：告警/报告邮件收件人（QUANT_SMTP_NOTIFY_TO；site_settings 同名键可覆盖，未配回退发件人）。</summary>
    public string? SmtpNotifyTo { get; init; }

    /// <summary>batch3 流 A：API 限流（QUANT_RATE_LIMIT_PER_MIN），用户+IP 双维度，默认 100 req/min。</summary>
    public int RateLimitPerMin { get; init; } = 100;

    /// <summary>batch3 流 A：慢查询聚合告警 p95 阈值毫秒（QUANT_SLOW_QUERY_ALERT_P95_MS），默认 2000。</summary>
    public int SlowQueryAlertP95Ms { get; init; } = 2000;

    /// <summary>batch3 流 A：慢查询聚合告警最小时次数（QUANT_SLOW_QUERY_ALERT_MIN_COUNT），默认 20/小时。</summary>
    public int SlowQueryAlertMinCount { get; init; } = 20;

    /// <summary>batch3 流 A：模型去重缓存 TTL 分钟（QUANT_MODEL_CACHE_TTL_MIN），默认 60。</summary>
    public int ModelCacheTtlMinutes { get; init; } = 60;

    /// <summary>batch3 流 A：模型去重缓存开关（QUANT_MODEL_CACHE_ENABLED），默认 true。</summary>
    public bool ModelCacheEnabled { get; init; } = true;

    /// <summary>batch3 #11：低优任务最大等待分钟（QUANT_TASK_QUEUE_MAX_LOW_WAIT_MIN），默认 120；
    /// 排队超时的 normal 任务在 Claim 时自动提级为 high 一次（starvation 防护）。</summary>
    public int TaskQueueMaxLowWaitMin { get; init; } = 120;

    /// <summary>batch4 流 B #8：公开数据类 GET 接口 ETag/Last-Modified 开关（QUANT_ETAG_ENABLED），默认 true。</summary>
    public bool EtagEnabled { get; init; } = true;

    /// <summary>
    /// 从 IConfiguration + 环境变量绑定。连接串缺失直接抛错退出，
    /// 不再静默回退默认连接串（E7 fail-fast）。
    /// </summary>
    public static QuantOptions Bind(IConfiguration configuration, string[] args)
    {
        // --export-openapi 只生成路由表：跳过连接串 fail-fast（构建期产物导出）。
        var exportOnly = args.Contains("--export-openapi", StringComparer.Ordinal);
        var connectionString = configuration["ConnectionStrings:Default"]
            ?? Environment.GetEnvironmentVariable("QUANT_PG_CONNECTION");
        if (!exportOnly && string.IsNullOrWhiteSpace(connectionString))
            throw new InvalidOperationException(
                "缺少数据库连接串：请设置 ConnectionStrings:Default 或环境变量 QUANT_PG_CONNECTION。" +
                "（E7：不再静默回退 Host=localhost 默认连接串，配错环境变量必须快速失败。）");

        return new QuantOptions
        {
            ConnectionString = connectionString ?? "",
            Domain = configuration["QUANT_DOMAIN"],
            SiteRoot = configuration["QUANT_SITE_ROOT"],
            LogDir = configuration["QUANT_LOG_DIR"],
            SecretRoot = configuration["QUANT_SECRET_ROOT"],
            BackupDir = configuration["QUANT_BACKUP_DIR"],
            WebRoot = Environment.GetEnvironmentVariable("QUANT_WEB_ROOT"),
            ModelBudgetWarnRatio = Ratio(configuration["QUANT_MODEL_BUDGET_WARN_RATIO"], 0.8),
            PublicSourceFailAlertRounds = Int(configuration["QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS"], 3, 1, 100),
            CalendarAutoAccept = Bool(configuration["QUANT_CALENDAR_AUTO_ACCEPT"], true),
            ValuationCacheTtlMinutes = Int(configuration["QUANT_VALUATION_CACHE_TTL_MIN"], 360, 1, 43200),
            RedemptionRemindDays = Int(configuration["QUANT_REDEMPTION_REMIND_DAYS"], 14, 1, 90),
            HealthCheckCron = configuration["QUANT_HEALTH_CHECK_CRON"] ?? "0 6 * * 1",
            TotpIssuer = configuration["QUANT_TOTP_ISSUER"] ?? "观澜",
            AuditHashAlgo = configuration["QUANT_AUDIT_HASH_ALGO"] ?? "SHA256",
            SnapshotRetentionTickDays = Int(configuration["QUANT_SNAPSHOT_RETENTION_TICK_DAYS"], 90, 1, 3650),
            RiskMetricsCacheTtlMinutes = Int(configuration["QUANT_RISK_METRICS_CACHE_TTL_MIN"], 60, 1, 1440),
            SmtpHost = configuration["QUANT_SMTP_HOST"],
            SmtpPort = OptInt(configuration["QUANT_SMTP_PORT"], 1, 65535),
            SmtpUser = configuration["QUANT_SMTP_USER"],
            SmtpPass = configuration["QUANT_SMTP_PASS"],
            SmtpFrom = configuration["QUANT_SMTP_FROM"],
            SmtpNotifyTo = configuration["QUANT_SMTP_NOTIFY_TO"],
            RateLimitPerMin = Int(configuration["QUANT_RATE_LIMIT_PER_MIN"], 100, 1, 100000),
            SlowQueryAlertP95Ms = Int(configuration["QUANT_SLOW_QUERY_ALERT_P95_MS"], 2000, 1, 600000),
            SlowQueryAlertMinCount = Int(configuration["QUANT_SLOW_QUERY_ALERT_MIN_COUNT"], 20, 1, 1000000),
            ModelCacheTtlMinutes = Int(configuration["QUANT_MODEL_CACHE_TTL_MIN"], 60, 1, 1440),
            ModelCacheEnabled = Bool(configuration["QUANT_MODEL_CACHE_ENABLED"], true),
            TaskQueueMaxLowWaitMin = Int(configuration["QUANT_TASK_QUEUE_MAX_LOW_WAIT_MIN"], 120, 1, 1440),
            EtagEnabled = Bool(configuration["QUANT_ETAG_ENABLED"], true),
        };
    }

    private static double Ratio(string? raw, double fallback) =>
        double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) && value is > 0 and < 1
            ? value : fallback;

    private static int Int(string? raw, int fallback, int min, int max) =>
        int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value) && value >= min && value <= max
            ? value : fallback;

    private static int? OptInt(string? raw, int min, int max) =>
        int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value) && value >= min && value <= max
            ? value : null;

    private static bool Bool(string? raw, bool fallback) =>
        bool.TryParse(raw, out var value) ? value : fallback;
}
