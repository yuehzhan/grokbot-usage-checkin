<#
.SYNOPSIS
  Stops the background page and sampler started by start.ps1 or by the logon task.
#>
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPy)) { Write-Host 'Not installed (.venv missing).'; exit 1 }
$env:PYTHONUTF8 = '1'
$out = & $venvPy service.py stop --config (Join-Path $PSScriptRoot 'usage_config.json')
try { $r = $out | ConvertFrom-Json } catch { $r = $null }
if ($r -and $r.ok) {
    if ($r.stopped -and @($r.stopped).Count -gt 0) { Write-Host 'Stopped.' -ForegroundColor Green }
    else { Write-Host 'Nothing was running.' }
} else {
    Write-Host $out
    exit 1
}
