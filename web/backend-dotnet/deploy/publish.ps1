param([Parameter(Mandatory=$true)][string]$Destination, [string]$Dotnet = 'dotnet', [string]$Python = '3.12', [string]$Uv = 'uv', [ValidateSet('windows','linux')][string]$Target = 'windows')
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$releasePath = [System.IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $releasePath) { throw 'Publish requires a new release directory.' }
if ($releasePath.Equals($repoPath, [System.StringComparison]::OrdinalIgnoreCase) -or $releasePath.StartsWith($repoPath + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Publish outside the source checkout.' }
New-Item -ItemType Directory -Path $releasePath | Out-Null
& $Dotnet publish (Join-Path $repoPath 'web/backend-dotnet/src/QuantAssistant.Api') -c Release -p:UseAppHost=false -o (Join-Path $releasePath 'api')
if ($LASTEXITCODE -ne 0) { throw 'API publication failed.' }
if ($Target -eq 'windows') {
    & $Dotnet publish (Join-Path $repoPath 'web/backend-dotnet/src/QuantAssistant.NativeService') -c Release -o (Join-Path $releasePath 'service')
    if ($LASTEXITCODE -ne 0) { throw 'Windows service adapter publication failed.' }
}
$previousOutput = $env:QUANT_WEB_OUTPUT_DIR
$previousApiBase = $env:VITE_API_BASE_URL
try {
    if ([string]::IsNullOrWhiteSpace($env:VITE_API_BASE_URL)) { $env:VITE_API_BASE_URL = '/api/v1' }
    $env:QUANT_WEB_OUTPUT_DIR = Join-Path $releasePath 'frontend'
    Push-Location (Join-Path $repoPath 'web/frontend')
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        & npm.cmd test
        if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
        & npm.cmd run typecheck
        if ($LASTEXITCODE -ne 0) { throw 'Frontend type checking failed.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
} finally { $env:QUANT_WEB_OUTPUT_DIR = $previousOutput; $env:VITE_API_BASE_URL = $previousApiBase }
Copy-Item -LiteralPath (Join-Path $releasePath 'frontend/build') -Destination (Join-Path $releasePath 'api/wwwroot') -Recurse
Copy-Item -LiteralPath (Join-Path $repoPath 'web/backend-dotnet/migrations') -Destination $releasePath -Recurse
Copy-Item -LiteralPath (Join-Path $repoPath 'web/backend-dotnet/tools') -Destination $releasePath -Recurse
Copy-Item -LiteralPath (Join-Path $repoPath 'web/backend-dotnet/deploy') -Destination $releasePath -Recurse
Copy-Item -LiteralPath (Join-Path $repoPath 'web/worker') -Destination $releasePath -Recurse
$backendPath = Join-Path $releasePath 'backend'
New-Item -ItemType Directory -Path $backendPath | Out-Null
foreach ($part in @('pyproject.toml', 'uv.lock', 'src')) {
    Copy-Item -LiteralPath (Join-Path $repoPath ('web/backend/' + $part)) -Destination $backendPath -Recurse
}
if ($Target -eq 'windows') {
    & $Uv sync --project $backendPath --frozen --no-dev --python $Python
    if ($LASTEXITCODE -ne 0) { throw 'Locked Python dependency installation failed.' }
} else {
    # Virtual environments are platform- and path-specific. Prepare this on Ubuntu.
    & $Uv lock --project $backendPath --check --python $Python
    if ($LASTEXITCODE -ne 0) { throw 'Locked Python dependency verification failed.' }
}
Write-Output "Published release: $releasePath"
