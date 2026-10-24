<#
.SYNOPSIS
  Starts the local page and the hourly sampler in the background (no console
  windows) and opens the page in your browser. Run stop.ps1 to stop them.
#>
param(
    [switch]$NoBrowser,
    # Only the page; no automatic hourly reading.
    [switch]$NoSampler
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host 'Not installed yet. Run:  powershell -ExecutionPolicy Bypass -File .\install.ps1' -ForegroundColor Red
    exit 1
}
$env:PYTHONUTF8 = '1'
$argsList = @('service.py', 'start', '--config', (Join-Path $PSScriptRoot 'usage_config.json'))
if (-not $NoBrowser) { $argsList += '--open' }
if ($NoSampler) { $argsList += '--no-sampler' }
$out = & $venvPy @argsList
$code = $LASTEXITCODE
try { $r = $out | ConvertFrom-Json } catch { $r = $null }
if ($code -eq 0 -and $r -and $r.ok) {
    Write-Host "Running: $($r.url)" -ForegroundColor Green
    Write-Host 'Stop with:  powershell -ExecutionPolicy Bypass -File .\stop.ps1'
} else {
    Write-Host 'Could not start.' -ForegroundColor Red
    if ($out) { Write-Host $out }
    Write-Host 'Logs: usage-data\service.log, usage-data\server.log, usage-data\sampler.log'
    exit 1
}
