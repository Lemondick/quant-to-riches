param([string]$Release = $env:QUANT_RELEASE_DIR, [switch]$SkipBuild, [switch]$PreviewUI, [int]$Port = 8765,
    [ValidateRange(1, 3)][int]$Workers = 3, [string]$Dotnet = 'dotnet')

& (Join-Path $PSScriptRoot 'web/start.ps1') -Release $Release -SkipBuild:$SkipBuild -PreviewUI:$PreviewUI -Port $Port -Workers $Workers -Dotnet $Dotnet
