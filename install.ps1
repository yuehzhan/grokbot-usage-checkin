<#
.SYNOPSIS
  One-time setup on Windows: checks Python 3.10+, creates .venv, installs
  requirements.txt and creates usage_config.json from the example.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Manual
#>
param(
    # Do not try to read Grok Build automatically; you type the weekly % yourself.
    [switch]$Manual
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Test-Python([string]$exe, [string[]]$pre) {
    # Returns the version string ("3.12") when $exe runs Python >= 3.10, else $null.
    # The Microsoft Store "python.exe" alias prints nothing / opens the Store, so it fails here.
    try {
        $out = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2]) if sys.version_info >= (3, 10) else print('old')" 2>$null
    } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $v = "$out".Trim()
    if ($v -eq 'old') { return $null }
    return $v
}

$py = $null; $pyArgs = @()
foreach ($cand in @(@('py', @('-3')), @('python', @()), @('python3', @()))) {
    if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
        $ver = Test-Python $cand[0] $cand[1]
        if ($ver) { $py = $cand[0]; $pyArgs = $cand[1]; break }
    }
}
if (-not $py) {
    Write-Host 'Python 3.10 or newer was not found.' -ForegroundColor Red
    Write-Host 'Install it from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH"),'
    Write-Host 'open a NEW PowerShell window and run install.ps1 again.'
    exit 1
}
Write-Host "Using Python $ver ($py $($pyArgs -join ' '))"

$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host 'Creating virtual environment .venv ...'
    & $py @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host 'Could not create .venv' -ForegroundColor Red; exit 1 }
}

Write-Host 'Installing requirements (matplotlib, numpy, pywinpty, tzdata) ...'
& $venvPy -m pip install --disable-pip-version-check --quiet --upgrade pip
& $venvPy -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host 'pip install failed (see messages above).' -ForegroundColor Red; exit 1 }

$cfg = Join-Path $PSScriptRoot 'usage_config.json'
if (-not (Test-Path -LiteralPath $cfg)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'usage_config.example.json') -Destination $cfg
    Write-Host 'Created usage_config.json from usage_config.example.json'
} else {
    Write-Host 'usage_config.json already exists - left unchanged.'
}

# Is Grok Build on PATH? (Only asks Windows where "grok" is; nothing of Grok Build is read.)
$grok = $env:GROK_BIN
if (-not $grok) {
    $c = Get-Command grok -ErrorAction SilentlyContinue
    if ($c) { $grok = $c.Source }
}
if ($Manual -or -not $grok) {
    if (-not $Manual) {
        Write-Host ''
        Write-Host 'Grok Build ("grok") is not on PATH, so automatic reading cannot work.' -ForegroundColor Yellow
        Write-Host 'Switching to manual mode: enter your weekly % on the page ("Enter manually").'
        Write-Host 'If you install Grok Build later, set "pct_source" to "auto" in usage_config.json.'
    }
    & $venvPy -c "import json,sys; p=sys.argv[1]; d=json.load(open(p,encoding='utf-8')); d['pct_source']='manual'; json.dump(d,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)" $cfg
    Write-Host 'usage_config.json: pct_source = manual'
} else {
    Write-Host "Grok Build found: $grok"
}

& $venvPy -c "import matplotlib, numpy, zoneinfo; import winpty; print('Self-check OK')"
if ($LASTEXITCODE -ne 0) { Write-Host 'Self-check failed: a package did not import.' -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host 'Done. Start it with:   powershell -ExecutionPolicy Bypass -File .\start.ps1' -ForegroundColor Green
