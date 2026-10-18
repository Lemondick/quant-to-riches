param(
    [string]$Release = $env:QUANT_RELEASE_DIR,
    [switch]$PreviewUI,
    [switch]$SkipBuild,
    [int]$Port = 8765,
    [ValidateRange(1, 3)][int]$Workers = 3,
    [string]$Dotnet = 'dotnet'
)

$ErrorActionPreference = 'Stop'
if ($PreviewUI) {
    Push-Location (Join-Path $PSScriptRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules/vite/package.json')) {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Vue dependency installation failed.' }
        }
        if (-not $SkipBuild) {
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Vue frontend build failed.' }
        }
        & npm.cmd run preview:ui
        if ($LASTEXITCODE -ne 0) { throw 'Vue frontend preview failed.' }
    } finally { Pop-Location }
    return
}
if (-not $Release) {
    throw '请设置 QUANT_RELEASE_DIR 或传入 -Release，指向 publish.ps1 生成的原生发布目录。部署步骤见 web/docs/refactor/p7-runbook.md。'
}
if ($Port -lt 1 -or $Port -gt 65535) { throw '端口必须在 1–65535 之间。' }
# Published releases already contain the compiled API and frontend. Retain the
# old switch for existing shortcuts; publication is a separate operation.
$env:ASPNETCORE_URLS = "http://127.0.0.1:$Port"
$env:QUANT_INTERNAL_API_URL = "http://127.0.0.1:$Port"
& (Join-Path $PSScriptRoot 'backend-dotnet/deploy/run-native.ps1') -Release $Release -Dotnet $Dotnet -Workers $Workers
