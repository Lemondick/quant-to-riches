$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Commit = "e3981af1e4c94656cdc663f1466d54fd49253dd5"
$Prefix = if ($env:QUANT_TRADINGAGENTS_HOME) { $env:QUANT_TRADINGAGENTS_HOME } else { Join-Path $env:LOCALAPPDATA "QuantToRiches\TradingAgents" }
$Venv = if ($env:QUANT_TRADINGAGENTS_VENV) { $env:QUANT_TRADINGAGENTS_VENV } else { Join-Path $Prefix "venv" }
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

if (-not (Test-Path (Join-Path $Root "tradingagents-worker\runner.py"))) {
    throw "TradingAgents runner.py not found under $Root"
}
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
& $Python -m venv $Venv
$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install "git+https://github.com/simonlin1212/TradingAgents-astock.git@$Commit"
Copy-Item (Join-Path $Root "tradingagents-worker\runner.py") (Join-Path $Prefix "runner.py") -Force

Write-Output "Installed TradingAgents-Astock commit $Commit"
Write-Output "Set QUANT_TRADINGAGENTS_PYTHON=$VenvPython"
Write-Output "Set QUANT_TRADINGAGENTS_RUNNER=$(Join-Path $Prefix 'runner.py')"
Write-Output "After validating budgets and provider access, set QUANT_TRADINGAGENTS_ENGINE=full"
