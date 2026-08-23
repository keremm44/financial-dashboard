$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $env:FINANCIAL_DASHBOARD_CACHE) {
    $env:FINANCIAL_DASHBOARD_CACHE = Join-Path $repoRoot ".cache\live-smoke-15m"
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment Python not found: $python"
}

Write-Host "Financial Dashboard cache: $env:FINANCIAL_DASHBOARD_CACHE"
& $python -m streamlit run "src\financial_dashboard\ui\app.py"
