using System.Diagnostics;
using System.Text.Json;
using Serilog;

// The service identity reads an ACL-protected configuration; secrets never appear in arguments.
if (args.Length != 2 || args[0] is not ("--config" or "--validate-config")) throw new ArgumentException("Usage: --config ABSOLUTE_JSON_FILE");
var configPath = Path.GetFullPath(args[1]);
if (new FileInfo(configPath).Length > 65536) throw new InvalidDataException("Service configuration exceeds 64KB");
var settings = JsonSerializer.Deserialize<ServiceSettings>(File.ReadAllText(configPath), new JsonSerializerOptions(JsonSerializerDefaults.Web))
    ?? throw new InvalidDataException("Missing service configuration");
settings.Validate();
if (args[0] == "--validate-config") { Console.WriteLine("native_service_configuration_valid"); return; }
foreach (var (name, value) in settings.Environment) System.Environment.SetEnvironmentVariable(name, value);
var logs = Path.GetFullPath(settings.Environment["QUANT_LOG_DIR"]);
Directory.CreateDirectory(logs);
var builder = Host.CreateApplicationBuilder();
builder.Services.AddWindowsService(options => options.ServiceName = settings.ServiceName);
builder.Services.AddSerilog(configuration => configuration.MinimumLevel.Information()
    .WriteTo.File(Path.Combine(logs, "host-.log"), rollingInterval: RollingInterval.Day,
        fileSizeLimitBytes: 20_000_000, rollOnFileSizeLimit: true, retainedFileCountLimit: 30));
builder.Services.AddSingleton(settings);
builder.Services.AddHostedService<NativeProcessService>();
await builder.Build().RunAsync();

sealed record ServiceSettings(string Release, string Dotnet, int Workers, Dictionary<string, string> Environment,
    string ServiceName = "QuantAssistantNative")
{
    public void Validate()
    {
        if (Workers is < 1 or > 3 || !Path.IsPathFullyQualified(Release) || !Path.IsPathFullyQualified(Dotnet)
            || !File.Exists(Dotnet) || !File.Exists(Path.Combine(Release, "api", "QuantAssistant.Api.dll"))
            || !File.Exists(Path.Combine(Release, "backend", ".venv", "Scripts", "python.exe"))
            || !System.Text.RegularExpressions.Regex.IsMatch(ServiceName, "^[A-Za-z][A-Za-z0-9_-]{0,63}$"))
            throw new InvalidDataException("Invalid native release, runtime or service name");
        foreach (var key in new[] { "QUANT_PG_CONNECTION", "QUANT_SITE_ROOT", "QUANT_SECRET_ROOT", "QUANT_LOG_DIR", "QUANT_WORKER_SECRET" })
            if (!Environment.TryGetValue(key, out var value) || string.IsNullOrWhiteSpace(value)) throw new InvalidDataException("Required environment is missing");
        if (Environment["QUANT_WORKER_SECRET"].Length < 32) throw new InvalidDataException("Worker secret is too short");
        foreach (var key in new[] { "QUANT_SITE_ROOT", "QUANT_SECRET_ROOT", "QUANT_LOG_DIR" })
            if (!Path.IsPathFullyQualified(Environment[key])) throw new InvalidDataException("Service directories must be absolute");
        if (Environment.Any(pair => !pair.Key.StartsWith("QUANT_", StringComparison.Ordinal)
            && !pair.Key.StartsWith("ASPNETCORE_", StringComparison.Ordinal)
            && !new[] { "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSFILE", "PATH" }.Contains(pair.Key)))
            throw new InvalidDataException("Unexpected environment name");
    }
}

sealed class NativeProcessService(ServiceSettings settings, ILogger<NativeProcessService> logger) : BackgroundService
{
    private async Task<int> Run(ProcessStartInfo start, CancellationToken cancellation)
    {
        start.UseShellExecute = false; start.CreateNoWindow = true;
        start.RedirectStandardOutput = true; start.RedirectStandardError = true;
        using var process = new Process { StartInfo = start };
        process.OutputDataReceived += (_, line) => { if (line.Data is { } value) logger.LogInformation("{NativeOutput}", value[..Math.Min(value.Length, 4096)]); };
        process.ErrorDataReceived += (_, line) => { if (line.Data is { } value) logger.LogWarning("{NativeOutput}", value[..Math.Min(value.Length, 4096)]); };
        process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
        try { await process.WaitForExitAsync(cancellation); process.WaitForExit(); return process.ExitCode; }
        finally { if (!process.HasExited) { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(CancellationToken.None); } }
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        try
        {
            var migrate = new ProcessStartInfo(settings.Dotnet) { WorkingDirectory = Path.Combine(settings.Release, "api") };
            migrate.ArgumentList.Add(Path.Combine(settings.Release, "api", "QuantAssistant.Api.dll"));
            migrate.ArgumentList.Add("--migrate"); migrate.ArgumentList.Add(Path.Combine(settings.Release, "migrations"));
            if (await Run(migrate, stoppingToken) != 0) throw new InvalidOperationException("Native migration failed");
            System.Environment.SetEnvironmentVariable("QUANT_WEB_ROOT", Path.Combine(settings.Release, "api", "wwwroot"));
            System.Environment.SetEnvironmentVariable("QUANT_EXPECTED_WORKERS", settings.Workers.ToString());
            System.Environment.SetEnvironmentVariable("ASPNETCORE_ENVIRONMENT", "Production");
            var start = new ProcessStartInfo(Path.Combine(settings.Release, "backend", ".venv", "Scripts", "python.exe")) { WorkingDirectory = settings.Release };
            foreach (var arg in new[] { Path.Combine(settings.Release, "worker", "native_host.py"), "--release", settings.Release, "--dotnet", settings.Dotnet, "--workers", settings.Workers.ToString() }) start.ArgumentList.Add(arg);
            await Run(start, stoppingToken);
            if (!stoppingToken.IsCancellationRequested) throw new InvalidOperationException("Native supervisor exited unexpectedly");
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
        catch (Exception error)
        {
            logger.LogError("Native service stopped ({FailureType}); Windows service recovery must restart it", error.GetType().Name);
            // SCM recovery requires a nonzero process exit, rather than a normal host stop.
            Log.CloseAndFlush(); System.Environment.Exit(1);
        }
    }
}
