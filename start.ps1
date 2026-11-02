<#
.SYNOPSIS
  Starts the local page and the hourly sampler in the background (no console
  windows) and opens the page in your browser. Run stop.ps1 to stop them.
  The page address (the port changes on every start) is printed, shown in a small
  notification, and the window stays open a few seconds so you can read it.
#>
param(
    [switch]$NoBrowser,
    # Only the page; no automatic hourly reading.
    [switch]$NoSampler,
    # Do not keep this window open for a few seconds after starting.
    [switch]$NoWait
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
    $url = $r.url
    Write-Host ''
    Write-Host 'Grok Usage Dashboard is running. Open this address in your browser:' -ForegroundColor Green
    Write-Host "    $url" -ForegroundColor Green
    Write-Host '(The number after the colon changes every time it starts; do not bookmark it.)'
    Write-Host 'Stop with:  powershell -ExecutionPolicy Bypass -File .\stop.ps1'
    if (-not $NoBrowser -and $url) {
        # service.py already tried to open it; if that failed, try once more from here.
        if ($r.opened -ne $true) { try { Start-Process -FilePath $url } catch { } }
        # A small, non-blocking notice with the address in case no browser window shows up.
        $notify = $true
        try { if ((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'usage_config.json') -Raw | ConvertFrom-Json).desktop_notify -eq $false) { $notify = $false } } catch { }
        if ($notify) {
            try {
                Start-Process -FilePath $venvPy -WindowStyle Hidden -ArgumentList @('desktop_notify.py', 'Grok Usage Dashboard is running', "Open $url in your browser if no window appeared.")
            } catch { }
        }
        if (-not $NoWait) { Start-Sleep -Seconds 6 }
    }
} else {
    Write-Host 'Could not start.' -ForegroundColor Red
    if ($out) { Write-Host $out }
    Write-Host 'Logs: usage-data\service.log, usage-data\server.log, usage-data\sampler.log'
    if (-not $NoBrowser -and -not $NoWait) { Start-Sleep -Seconds 10 }
    exit 1
}
