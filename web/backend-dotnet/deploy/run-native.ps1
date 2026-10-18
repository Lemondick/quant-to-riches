param([Parameter(Mandatory=$true)][string]$Release, [string]$Dotnet = 'dotnet', [int]$Workers = 3)
$ErrorActionPreference = 'Stop'
$releasePath = (Resolve-Path -LiteralPath $Release).Path
if (-not $env:QUANT_PG_CONNECTION -or -not $env:QUANT_SITE_ROOT -or $env:QUANT_WORKER_SECRET.Length -lt 32) {
    throw 'Set QUANT_PG_CONNECTION, QUANT_SITE_ROOT and a worker secret of at least 32 characters.'
}
if ($Workers -lt 1 -or $Workers -gt 3) { throw 'Workers must be between 1 and 3.' }
if (-not $env:ASPNETCORE_URLS) { $env:ASPNETCORE_URLS = 'http://127.0.0.1:8765' }
if (-not $env:QUANT_INTERNAL_API_URL) { $env:QUANT_INTERNAL_API_URL = 'http://127.0.0.1:8765' }
$env:QUANT_WEB_ROOT = Join-Path $releasePath 'api/wwwroot'
$env:ASPNETCORE_ENVIRONMENT = 'Production'
$env:QUANT_EXPECTED_WORKERS = [string]$Workers
& $Dotnet (Join-Path $releasePath 'api/QuantAssistant.Api.dll') --migrate (Join-Path $releasePath 'migrations')
if ($LASTEXITCODE -ne 0) { throw 'Migration failed; services were not started.' }
$pythonPath = Join-Path $releasePath 'backend/.venv/Scripts/python.exe'
& $pythonPath (Join-Path $releasePath 'worker/native_host.py') --release $releasePath --dotnet $Dotnet --workers $Workers
if ($LASTEXITCODE -ne 0) { throw 'Native services stopped after a service failure. Inspect the service log before restarting.' }
