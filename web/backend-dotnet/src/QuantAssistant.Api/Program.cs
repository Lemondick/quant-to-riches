using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Errors;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Serialization;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.History;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Core.Operations;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Core.Runtime;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Health;
using QuantAssistant.Infrastructure.History;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Operations;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Runtime;
using QuantAssistant.Infrastructure.Security;
using Serilog;
using OpenTelemetry.Trace;
using Npgsql;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.AspNetCore.DataProtection;
using System.Threading.RateLimiting;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Infrastructure.Db;

var webRoot = Environment.GetEnvironmentVariable("QUANT_WEB_ROOT");
var builder = WebApplication.CreateBuilder(new WebApplicationOptions
{
    Args = args,
    WebRootPath = string.IsNullOrWhiteSpace(webRoot) ? null : Path.GetFullPath(webRoot),
});
// E7：集中配置 + 启动 fail-fast。连接串缺失直接抛错退出，不再静默回退默认连接串。
var options = QuantOptions.Bind(builder.Configuration, args);
builder.Services.AddSingleton(options);
if (options.SiteRoot is { Length: > 0 } siteRoot) Directory.CreateDirectory(Path.GetFullPath(siteRoot));builder.Services.Configure<ForwardedHeadersOptions>(options =>
{
    options.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto;
    options.ForwardLimit = 1; // Default trusted proxies are loopback only.
});
builder.Services.AddRateLimiter(options =>
{
    options.GlobalLimiter = PartitionedRateLimiter.Create<HttpContext, string>(context =>
        HttpMethods.IsPost(context.Request.Method) && context.Request.Path.Value?.TrimEnd('/').ToLowerInvariant() is
            "/api/v1/auth/login" or "/api/v1/auth/register" or "/api/v1/auth/recovery/redeem" or "/api/v1/auth/password"
            ? RateLimitPartition.GetTokenBucketLimiter(context.Connection.RemoteIpAddress?.ToString() ?? "unknown", _ => new()
            { TokenLimit = 20, TokensPerPeriod = 20, ReplenishmentPeriod = TimeSpan.FromMinutes(1), QueueLimit = 0 })
            : RateLimitPartition.GetNoLimiter("other"));
    options.OnRejected = async (context, cancellation) =>
    {
        context.HttpContext.Response.StatusCode = 429;
        await context.HttpContext.Response.WriteAsJsonAsync(new ErrorResponse("rate_limited", "请求过于频繁，请稍后重试。", context.HttpContext.GetRequestId()), cancellation);
    };
});
var connectionString = options.ConnectionString;

if (args.Length > 0 && args[0] == "--migrate")
{
    // E2：--migrate <目录> [--dry-run] [--rollback-to <版本>] [--no-transaction]
    if (args.Length < 2 || args[1].StartsWith("--", StringComparison.Ordinal))
    {
        Console.Error.WriteLine("用法：--migrate <目录> [--dry-run] [--rollback-to <版本>] [--no-transaction]");
        Environment.ExitCode = 2;
        return;
    }
    string? FlagValue(string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length && !args[index + 1].StartsWith("--", StringComparison.Ordinal)
            ? args[index + 1] : null;
    }
    var rollbackTo = FlagValue("--rollback-to");
    if (args.Contains("--rollback-to") && rollbackTo is null)
    {
        Console.Error.WriteLine("--rollback-to 需要一个版本号参数。");
        Environment.ExitCode = 2;
        return;
    }
    var migrateOptions = new MigrationRunner.MigrationOptions(
        DryRun: args.Contains("--dry-run"),
        RollbackTo: rollbackTo,
        NoTransaction: args.Contains("--no-transaction"));
    var label = migrateOptions.RollbackTo is not null
        ? (migrateOptions.DryRun ? "Would roll back" : "Rolled back")
        : migrateOptions.DryRun ? "Pending" : "Applied";
    foreach (var migration in new MigrationRunner(connectionString).Apply(args[1], migrateOptions))
        Console.WriteLine($"{label} {migration}");
    return;
}
if (QuantAssistant.Api.NativeCommands.Execute(args, connectionString)) return;

// 配置 Serilog
builder.Host.UseSerilog((context, services, configuration) =>
{
    var formatter = new Serilog.Formatting.Compact.CompactJsonFormatter();
    configuration.ReadFrom.Configuration(context.Configuration).ReadFrom.Services(services)
        .Enrich.FromLogContext().WriteTo.Console(formatter);
    if (options.LogDir is { Length: > 0 } logDirectory)
    {
        var path = Path.GetFullPath(logDirectory);
        Directory.CreateDirectory(path);
        configuration.WriteTo.File(formatter, Path.Combine(path, "api-.jsonl"), rollingInterval: RollingInterval.Day,
            fileSizeLimitBytes: 20_000_000, rollOnFileSizeLimit: true, retainedFileCountLimit: 30);
    }
});


// JSON 契约：snake_case + 拒绝额外字段（旧站点封闭模型行为）。
builder.Services
    .AddControllers(options => options.OutputFormatters.RemoveType<Microsoft.AspNetCore.Mvc.Formatters.HttpNoContentOutputFormatter>())
    .AddJsonOptions(options =>
    {
        options.JsonSerializerOptions.PropertyNamingPolicy = new SnakeCaseNamingPolicy();
        options.JsonSerializerOptions.UnmappedMemberHandling =
            System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow;
        options.JsonSerializerOptions.Converters.Add(new FinanceJson.DecimalConverter());
    });

// 模型校验失败 → 422 invalid_request（旧站点语义，而非默认 400 ProblemDetails）。
builder.Services.Configure<ApiBehaviorOptions>(options =>
{
    options.InvalidModelStateResponseFactory = context =>
    {
        var requestId = context.HttpContext.GetRequestId();
        var payload = new ErrorResponse(
            ErrorCodes.InvalidRequest,
            ErrorMessages.Get(ErrorCodes.InvalidRequest),
            requestId);
        return new JsonResult(payload) { StatusCode = StatusCodes.Status422UnprocessableEntity };
    };
});

builder.Services.AddMemoryCache();
builder.Services.AddSingleton<QuantAssistant.Api.Services.AccountDisplayCache>();
builder.Services.AddSingleton<NativeMetrics>();
builder.Services.AddHostedService<QuantAssistant.Api.Services.ThreadPoolMonitor>();
builder.Services.AddProblemDetails();
builder.Services.AddExceptionHandler<GlobalExceptionHandler>();
builder.Services.AddOpenApi();

builder.Services.AddSingleton<IReadinessProbe>(_ => new PostgresReadinessProbe(connectionString));

// P2 账号认证依赖：连接串来自配置 ConnectionStrings:Default 或环境 QUANT_PG_CONNECTION。
builder.Services.AddSingleton<IAccountRepository>(_ => new AccountRepository(connectionString));
builder.Services.AddSingleton<IPasswordHasher, Argon2PasswordHasher>();
builder.Services.AddSingleton<TimeProvider>(TimeProvider.System);
builder.Services.AddSingleton<AuthService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.UsageCounterService>();
builder.Services.AddHostedService(provider => provider.GetRequiredService<QuantAssistant.Api.Services.UsageCounterService>());

// P3 账本 + P4 任务/运行 + P5 历史/运行维护仓储。
builder.Services.AddSingleton<ILedgerRepository>(_ => new LedgerRepository(connectionString));
builder.Services.AddSingleton<IJobRepository>(_ => new JobRepository(connectionString));
builder.Services.AddSingleton<IOperationRepository>(_ => new OperationRepository(connectionString));
builder.Services.AddSingleton<IHistoryRepository>(_ => new HistoryRepository(connectionString));
builder.Services.AddSingleton<IRuntimeRepository>(_ => new RuntimeRepository(connectionString));
builder.Services.AddSingleton<QuantAssistant.Core.Portfolio.IFactorRepository>(sp => new QuantAssistant.Infrastructure.Portfolio.FactorRepository(connectionString, sp.GetRequiredService<Microsoft.Extensions.Caching.Memory.IMemoryCache>()));
builder.Services.AddSingleton<QuantAssistant.Core.Portfolio.IDcaRepository>(_ => new QuantAssistant.Infrastructure.Portfolio.DcaRepository(connectionString));
builder.Services.AddSingleton<QuantAssistant.Core.Portfolio.IPeerRepository>(sp => new QuantAssistant.Infrastructure.Portfolio.PeerRepository(connectionString, sp.GetRequiredService<Microsoft.Extensions.Caching.Memory.IMemoryCache>()));
builder.Services.AddSingleton<QuantAssistant.Core.Portfolio.IAttributionRepository>(sp => new QuantAssistant.Infrastructure.Portfolio.AttributionRepository(connectionString, sp.GetRequiredService<IAccountDatabase>()));
builder.Services.AddSingleton<TransactionService>();
builder.Services.AddSingleton(_ => new QuantAssistant.Infrastructure.Documents.UserDocumentRepository(connectionString));
builder.Services.AddSingleton(_ => new ComputeJobRepository(connectionString));
builder.Services.AddSingleton(_ => new AccountDatabase(connectionString));
// E5：Api 层只依赖 IAccountDatabase 抽象（依赖倒置）；具体类保留供 CLI 同步适配器使用。
builder.Services.AddSingleton<IAccountDatabase>(sp => sp.GetRequiredService<AccountDatabase>());
builder.Services.AddSingleton<ModelCostRepository>(sp => new ModelCostRepository(sp.GetRequiredService<AccountDatabase>(),
    sp.GetRequiredService<QuantAssistant.Api.Configuration.QuantOptions>().ModelBudgetWarnRatio));
builder.Services.AddSingleton<FundFeeRepository>();
builder.Services.AddSingleton<FundLotRepository>();
builder.Services.AddSingleton<ReconciliationRepository>();
builder.Services.AddSingleton<AnalysisSnapshotRepository>();
// E5：控制器 SQL 下沉后的仓储/服务（构造器注入 IAccountDatabase）。
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Portfolio.CashFlowPlanRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Portfolio.AlertCenterRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Portfolio.RebalanceRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Portfolio.SmartDcaRepository>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.SmartDcaService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.AskService>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Portfolio.AnnouncementRepository>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.AnnouncementService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.FundMaintenanceService>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Portfolio.DrawdownRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Research.ResearchMetadataRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Research.ResearchJobQueryRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Research.StrategyRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Research.FundResearchRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Research.FundExecutionReplayRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Research.DecisionRunRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Ledger.LedgerReadRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Operations.JobHeartbeatRepository>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.RuntimeViewService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.ComputeService>();
builder.Services.AddSingleton<PublicSnapshotRepository>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.FundCatalogService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.PublicMarketSnapshot>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.DashboardSummaryService>();
builder.Services.AddHostedService<QuantAssistant.Api.Services.PublicSnapshotRefresher>();
builder.Services.AddHostedService(provider => new QuantAssistant.Api.Services.ComputeNotificationListener(connectionString, provider.GetRequiredService<ILogger<QuantAssistant.Api.Services.ComputeNotificationListener>>()));
builder.Services.AddSingleton<QuantAssistant.Api.Services.AutomaticAttributionService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.LinkedAttributionService>();
builder.Services.AddSingleton<AdminDataRepository>();
builder.Services.AddSingleton<QuantAssistant.Infrastructure.Runtime.UserOperationAuditWriter>();
builder.Services.AddSingleton<TradingCalendarRepository>(sp => new TradingCalendarRepository(sp.GetRequiredService<AccountDatabase>(),
    sp.GetRequiredService<TimeProvider>(), sp.GetRequiredService<QuantAssistant.Api.Configuration.QuantOptions>().CalendarAutoAccept));
var keyProtection = builder.Services.AddDataProtection().SetApplicationName("QuantAssistant.Native");
if (options.SecretRoot is { Length: > 0 })
{
    var secretRoot = QuantAssistant.Api.Services.PrivateSecretStore.PrepareRoot(builder.Configuration);
    keyProtection.PersistKeysToFileSystem(new DirectoryInfo(Path.Combine(secretRoot, "protection-keys")));
    if (OperatingSystem.IsWindows()) keyProtection.ProtectKeysWithDpapi();
}
builder.Services.AddSingleton<QuantAssistant.Api.Services.PrivateSecretStore>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.PublicModelCatalog>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.ModelConnectionService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.AdvisorExecutionService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.ResearchExecutionService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.PhotoMigrationService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.FundNavMigrationService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.LedgerReplayMigrationService>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.NativeBackupEvidence>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.NativeOperationsMonitor>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.NativeRuntimeMaintenance>();
builder.Services.AddHostedService(provider => provider.GetRequiredService<QuantAssistant.Api.Services.NativeRuntimeMaintenance>());
builder.Services.AddSingleton<QuantAssistant.Api.Services.NativeSiteLock>();
builder.Services.AddSingleton<QuantAssistant.Api.Services.NativeScheduler>();
builder.Services.AddHostedService(provider=>provider.GetRequiredService<QuantAssistant.Api.Services.NativeScheduler>());

// 配置 OpenTelemetry
builder.Services.AddOpenTelemetry()
    .WithTracing(tracing =>
    {
        tracing.AddAspNetCoreInstrumentation().AddNpgsql();
        if (builder.Configuration["QUANT_TRACE_CONSOLE"] == "1" || builder.Environment.IsDevelopment() && builder.Configuration["QUANT_TRACE_CONSOLE"] != "0")
            tracing.AddConsoleExporter();
    });


var app = builder.Build();
var siteLock = app.Services.GetRequiredService<QuantAssistant.Api.Services.NativeSiteLock>();
if(QuantAssistant.Api.NativeSecretCommands.Execute(args,app.Services,connectionString))return;
if (siteLock.Held && builder.Configuration["QUANT_WORKER_SECRET"] is { Length: >= 32 })
    app.Services.GetRequiredService<AccountDatabase>().Write("compute:reaper", sql => sql.Execute("UPDATE worker_heartbeats SET stopping=true"));

app.UseForwardedHeaders();
// E16：异常处理器必须在 NativeMetrics/RequestId/Performance 之前，
// 否则三者内部抛出的异常会绕过统一错误契约返回裸 500。
app.UseExceptionHandler();
app.UseMiddleware<NativeMetricsMiddleware>();
app.UseSerilogRequestLogging(options =>
{
    options.GetLevel = (context, _, error) => error is not null || context.Response.StatusCode >= 500 ? Serilog.Events.LogEventLevel.Error
        : context.Request.Path.StartsWithSegments("/internal/v1/worker") && context.Response.StatusCode < 400
            ? Serilog.Events.LogEventLevel.Debug : Serilog.Events.LogEventLevel.Information;
    options.EnrichDiagnosticContext = (diagnostic, context) => diagnostic.Set("TraceId", context.Response.Headers["X-Trace-Id"].ToString());
});
app.UseMiddleware<RequestIdMiddleware>();
app.UseMiddleware<PerformanceMiddleware>();
app.UseMiddleware<SecurityHeadersMiddleware>();
app.UseRouting();
app.UseRateLimiter();
app.UseMiddleware<SessionAuthenticationMiddleware>();
app.UseMiddleware<AccountUsageMiddleware>();
app.UseDefaultFiles();
app.UseStaticFiles();
app.UseStatusCodePages(async statusContext =>
{
    var response = statusContext.HttpContext.Response;
    if (response.StatusCode is not (404 or 405))
    {
        return;
    }

    var code = response.StatusCode == 404 ? ErrorCodes.RouteNotFound : ErrorCodes.MethodNotAllowed;
    var requestId = statusContext.HttpContext.GetRequestId();
    var payload = new ErrorResponse(code, ErrorMessages.Get(code), requestId);
    response.ContentType = "application/json; charset=utf-8";
    await response.WriteAsJsonAsync(payload, JsonDefaults.Options);
});

app.MapControllers();
// Serve the Vue entry for its declared history routes. Unknown APIs and assets
// keep their existing JSON 404 contract.
foreach (var route in new[] { "/portfolio", "/ledger", "/watchlist", "/market", "/ai-stock", "/fund-review", "/quant", "/news", "/admin", "/settings" })
    app.MapFallbackToFile(route, "index.html");
if (app.Environment.IsDevelopment()) app.MapOpenApi("/openapi.json");

// E13：构建期导出版本化 OpenAPI 产物（web/backend-dotnet/artifacts/openapi.json），
// 供 D 的 API 控制台与 C 的 CI 校验使用。不需要数据库：只生成路由表。
if (args is ["--export-openapi", var openApiPath])
{
    await ExportOpenApiAsync(app, openApiPath);
    return;
}

app.Run();

// 构建期导出 openapi.json：直接在进程内调用 /openapi.json 端点的 RequestDelegate，
// 不经过 TCP（沙箱/CI 环境可能禁止回环 socket），也不启动后台服务（无需数据库）。
static async Task ExportOpenApiAsync(WebApplication app, string path)
{
    var full = Path.GetFullPath(path);
    Directory.CreateDirectory(Path.GetDirectoryName(full)!);
    app.MapOpenApi("/openapi.json");
    var endpoint = ((IEndpointRouteBuilder)app).DataSources.SelectMany(s => s.Endpoints).OfType<RouteEndpoint>()
        .FirstOrDefault(e => string.Equals(e.RoutePattern.RawText, "/openapi.json", StringComparison.Ordinal))
        ?? throw new InvalidOperationException("OpenAPI 导出：未找到 /openapi.json 端点。");
    var context = new DefaultHttpContext();
    context.RequestServices = app.Services;
    context.Request.Method = HttpMethods.Get;
    context.Request.Path = "/openapi.json";
    context.Response.Body = new MemoryStream();
    await endpoint.RequestDelegate!(context);
    context.Response.Body.Seek(0, SeekOrigin.Begin);
    var json = await new StreamReader(context.Response.Body).ReadToEndAsync();
    if (context.Response.StatusCode != StatusCodes.Status200OK || string.IsNullOrWhiteSpace(json))
        throw new InvalidOperationException($"OpenAPI 导出失败：HTTP {context.Response.StatusCode}。");
    await File.WriteAllTextAsync(full, json);
    Console.WriteLine($"Exported OpenAPI to {full} ({json.Length} chars)");
}

// 暴露给集成测试（WebApplicationFactory）。
public partial class Program;
