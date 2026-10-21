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
        };
    }

    private static double Ratio(string? raw, double fallback) =>
        double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) && value is > 0 and < 1
            ? value : fallback;

    private static int Int(string? raw, int fallback, int min, int max) =>
        int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value) && value >= min && value <= max
            ? value : fallback;

    private static bool Bool(string? raw, bool fallback) =>
        bool.TryParse(raw, out var value) ? value : fallback;
}
