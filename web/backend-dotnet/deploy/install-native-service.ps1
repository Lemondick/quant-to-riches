# Run elevated only when intentionally installing a production Windows service.
param([Parameter(Mandatory=$true)][string]$Config, [string]$ServiceName='QuantAssistantNative')
$ErrorActionPreference = 'Stop'
if ($ServiceName -notmatch '^[A-Za-z][A-Za-z0-9_-]{0,63}$') { throw 'Invalid service name.' }
$configPath = (Resolve-Path -LiteralPath $Config).Path
$settings = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($settings.serviceName -and $settings.serviceName -ne $ServiceName) { throw 'Configuration service name does not match.' }
if (-not [IO.Path]::IsPathFullyQualified($settings.release)) { throw 'Release must be absolute.' }
$exePath = Join-Path $settings.release 'service/QuantAssistant.NativeService.exe'
if (-not (Test-Path -LiteralPath $exePath)) { throw 'Publish the complete release first.' }
& $exePath --validate-config $configPath
if ($LASTEXITCODE -ne 0) { throw 'Service configuration is invalid.' }
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) { throw 'Service already exists; stop it and explicitly update its release configuration.' }
$binary = '"' + $exePath + '" --config "' + $configPath + '"'
# A virtual service identity has no stored account password and is not LocalSystem.
& sc.exe create $ServiceName binPath= $binary start= demand obj= "NT SERVICE\$ServiceName"
if ($LASTEXITCODE -ne 0) { throw 'Service registration failed.' }
& sc.exe sidtype $ServiceName unrestricted
if ($LASTEXITCODE -ne 0) { throw 'Service identity configuration failed.' }
$serviceIdentity = "NT SERVICE\$ServiceName"
# Protect the configuration itself and grant only the required dedicated paths.
& icacls.exe $configPath /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' "${serviceIdentity}:R"
if ($LASTEXITCODE -ne 0) { throw 'Configuration ACL setup failed.' }
& icacls.exe $settings.release /grant "${serviceIdentity}:(OI)(CI)RX"
if ($LASTEXITCODE -ne 0) { throw 'Release ACL setup failed.' }
foreach ($key in @('QUANT_SITE_ROOT','QUANT_SECRET_ROOT','QUANT_LOG_DIR','QUANT_BACKUP_DIR')) {
    $value = $settings.environment.$key
    if (-not $value) { continue }
    if (-not [IO.Path]::IsPathFullyQualified($value)) { throw 'Service directories must be absolute.' }
    New-Item -ItemType Directory -Path $value -Force | Out-Null
    $access = if ($key -eq 'QUANT_SECRET_ROOT') { 'F' } else { 'M' }
    & icacls.exe $value /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "${serviceIdentity}:(OI)(CI)$access"
    if ($LASTEXITCODE -ne 0) { throw 'Service directory ACL setup failed.' }
}
& sc.exe failure $ServiceName reset= 86400 actions= 'restart/5000/restart/15000/restart/60000'
if ($LASTEXITCODE -ne 0) { throw 'Service recovery setup failed.' }
& sc.exe config $ServiceName start= delayed-auto
if ($LASTEXITCODE -ne 0) { throw 'Automatic startup setup failed.' }
Write-Output 'Service installed but not started. Start with Start-Service, then configure model keys through the account UI under the new service identity before enabling calls.'
