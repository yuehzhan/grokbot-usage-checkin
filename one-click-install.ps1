<#
.SYNOPSIS
  One-click install for Windows (double-click Install-Windows.cmd, which runs this file).
  Checks the PC, shows what will be installed, then installs what is missing without asking, makes "auto"
  Grok Build's saved default mode and starts the dashboard. Safe to run again: finished steps are skipped
  and the "auto" default is put back if it was switched off.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\one-click-install.ps1            # installs what is missing, no questions
  powershell -ExecutionPolicy Bypass -File .\one-click-install.ps1 -Ask       # ask "Continue? [y/N]" first
  powershell -ExecutionPolicy Bypass -File .\one-click-install.ps1 -DryRun    # only show what would be done
  powershell -ExecutionPolicy Bypass -File .\one-click-install.ps1 -Manual    # type the weekly % yourself
  powershell -ExecutionPolicy Bypass -File .\one-click-install.ps1 -NoShortcut  # no Desktop shortcut
#>
param(
    # Ask "Continue? [y/N]" before installing anything (off by default).
    [switch]$Ask,
    # Kept so older instructions still work; nothing is asked by default now.
    [switch]$Yes,
    [switch]$DryRun,
    # You type the weekly % yourself; Grok Build is not installed or changed.
    [switch]$Manual,
    # Do not change Grok Build's default mode.
    [switch]$NoGrokAuto,
    [switch]$NoStart,
    # Do not put a "Grok Usage Dashboard" shortcut on the Desktop.
    [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$GrokInstallUrl = 'https://x.ai/cli/install.ps1'   # official installer (https://docs.x.ai/build/overview)

function Step([string]$t) { Write-Host ''; Write-Host "== $t ==" -ForegroundColor Cyan }
function Ok([string]$t) { Write-Host "   OK: $t" -ForegroundColor Green }
function Info([string]$t) { Write-Host "   $t" }
function Fail([string]$what, [string]$next) {
    Write-Host ''
    Write-Host "!! Problem: $what" -ForegroundColor Red
    Write-Host "   What to do: $next" -ForegroundColor Yellow
    Write-Host '   Your files were not deleted. When it is fixed, run the installer again; finished steps are skipped.'
    exit 1
}
function Refresh-Path {
    # Pick up PATH changes made by installers that ran after this window opened.
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($m, $u, (Join-Path $env:USERPROFILE '.grok\bin')) | Where-Object { $_ }) -join ';'
}
function Find-Python {
    foreach ($cand in @(@('py', @('-3')), @('python', @()), @('python3', @()))) {
        if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
            $pre = [string[]]$cand[1]
            try {
                $v = & $cand[0] @pre -c "import sys; print('%d.%d' % sys.version_info[:2]) if sys.version_info >= (3, 10) else print('old')" 2>$null
            } catch { $v = $null }
            if ($LASTEXITCODE -eq 0 -and $v -and "$v".Trim() -ne 'old') { return "$v".Trim() }
        }
    }
    return $null
}
function Find-Grok {
    if ($env:GROK_BIN -and (Test-Path -LiteralPath $env:GROK_BIN)) { return $env:GROK_BIN }
    $c = Get-Command grok -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path $env:USERPROFILE '.grok\bin\grok.exe'
    if (Test-Path -LiteralPath $p) { return $p }
    return $null
}

function New-DesktopShortcut {
    # "Grok Usage Dashboard.lnk" on the user's Desktop (also when OneDrive moved the Desktop), running start.ps1.
    # Made again on every run (it overwrites the old one). Any problem is only a warning: returns $null.
    # $env:ONECLICK_DESKTOP replaces the Desktop folder (tests use it, so they never touch the real Desktop).
    try {
        $desk = $env:ONECLICK_DESKTOP
        if (-not $desk) { $desk = [Environment]::GetFolderPath('Desktop') }
        if (-not $desk -or -not (Test-Path -LiteralPath $desk)) { Info 'No desktop shortcut: the Desktop folder was not found.'; return $null }
        $start = Join-Path $PSScriptRoot 'start.ps1'
        if (-not (Test-Path -LiteralPath $start)) { Info 'No desktop shortcut: start.ps1 is missing from this folder.'; return $null }
        $lnk = Join-Path $desk 'Grok Usage Dashboard.lnk'
        $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $sh = New-Object -ComObject WScript.Shell
        $sc = $sh.CreateShortcut($lnk)
        $sc.TargetPath = $psExe
        $sc.Arguments = "-ExecutionPolicy Bypass -File `"$start`""
        $sc.WorkingDirectory = $PSScriptRoot
        $sc.Description = 'Open the Grok usage dashboard'
        $ico = Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ico' -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($ico) { $sc.IconLocation = $ico.FullName } else { $sc.IconLocation = "$psExe,0" }
        $sc.Save()
        if (-not (Test-Path -LiteralPath $lnk)) { throw 'the shortcut file was not written' }
        return $lnk
    } catch {
        Info "Could not make the desktop shortcut ($($_.Exception.Message.Trim())). Not a problem: start the dashboard with start.ps1 in this folder."
        return $null
    }
}

function Disable-QuickEdit {
    # With QuickEdit on, a click inside the console window freezes the running script until a key is pressed,
    # which would stop "one run start to finish". Turn it off for THIS console window only: nothing is written to
    # the registry or to the console defaults, and it ends with this window. Any failure is harmless.
    try {
        if ($env:OS -ne 'Windows_NT') { return }
        if (-not ('OneClickConsole' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class OneClickConsole {
    [DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int nStdHandle);
    [DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint mode);
    [DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint mode);
}
'@
        }
        $h = [OneClickConsole]::GetStdHandle(-10)   # STD_INPUT_HANDLE
        [uint32]$mode = 0
        if ([OneClickConsole]::GetConsoleMode($h, [ref]$mode)) {
            # clear ENABLE_QUICK_EDIT_MODE (0x40), keep everything else, set ENABLE_EXTENDED_FLAGS (0x80)
            $new = [uint32](($mode -bor 0x80) - ($mode -band 0x40))
            [void][OneClickConsole]::SetConsoleMode($h, $new)
        }
    } catch { }
}
Disable-QuickEdit

Write-Host 'Grok Bot Usage & Check-in: one-click install (Windows)'
if ($DryRun) { Write-Host '(dry run: nothing will be changed)' }

# ---------- 1. look around (changes nothing) ----------
Step '1/5 Checking this PC'
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$pyVer = Find-Python
$needPython = -not $pyVer
$hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
if ($needPython) {
    if (-not $hasWinget) {
        Fail 'Python 3.10 or newer is not installed.' 'Install it from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH"), then double-click Install-Windows.cmd again.'
    }
    Info 'Python: not installed yet'
} else { Ok "Python $pyVer" }

$grok = Find-Grok
$needGrok = $false
if ($Manual) { Info 'Grok Build: not needed (manual mode)' }
elseif ($grok) { Ok "Grok Build is installed: $grok" }
else { $needGrok = $true; Info 'Grok Build: not installed yet' }

$needSetup = -not (Test-Path -LiteralPath $venvPy)
if ($needSetup) { Info 'Dashboard programs: not set up yet' } else { Ok 'Dashboard programs are set up (.venv)' }

# ---------- 2. say what will happen (asks only with -Ask) ----------
Step '2/5 What this installer will do now (no questions asked)'
$n = 0
if ($needPython) { $n++; Info "$n. Install Python 3.12 with Microsoft's winget:  winget install -e --id Python.Python.3.12" }
if ($needGrok) {
    $n++; Info "$n. Install Grok Build, xAI's official app, with xAI's own installer:"
    Info "     irm $GrokInstallUrl | iex"
    Info '     (puts grok.exe in your user folder under .grok\bin and adds it to your PATH; no admin rights)'
}
$n++; Info "$n. Set up the dashboard (install.ps1: .venv and its Python packages; already-done parts are skipped)"
$n++; Info "$n. Set reading to $(if ($Manual) { 'manual' } else { 'auto' }) in usage_config.json"
if (-not $Manual -and -not $NoGrokAuto) {
    $n++; Info "$n. Make 'auto' Grok Build's saved default mode (only that one setting in .grok\config.toml; your sign-in data is never opened)"
}
if (-not $NoShortcut) { $n++; Info "$n. Put a ""Grok Usage Dashboard"" shortcut on your Desktop (it runs start.ps1 from this folder)" }
if (-not $NoStart) { $n++; Info "$n. Start the dashboard on this PC only (127.0.0.1) and open it in your browser" }

if ($DryRun) { Write-Host ''; Write-Host 'Dry run finished. Nothing was changed.'; exit 0 }
if ($Ask -and ($needPython -or $needGrok -or $needSetup)) {
    Write-Host ''
    $ans = Read-Host 'Continue? [y/N]'
    if ($ans -notmatch '^(y|yes)$') { Write-Host 'Stopped. Nothing was installed.'; exit 1 }
}

# ---------- 3. Python + Grok Build ----------
Step '3/5 Python and Grok Build'
if ($needPython) {
    Info 'Installing Python 3.12 ...'
    & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $pyVer = Find-Python
    if (-not $pyVer) { Fail 'Python was installed but this window cannot see it yet.' 'Close this window and double-click Install-Windows.cmd again.' }
    Ok "Python $pyVer installed"
} else { Ok 'Python already installed, skipped' }
$justInstalledGrok = $false
if ($needGrok) {
    Info 'Downloading and installing Grok Build from x.ai ...'
    # Its own PowerShell process, so the official script's "exit" cannot end this one.
    & powershell -NoProfile -ExecutionPolicy Bypass -Command "irm $GrokInstallUrl | iex"
    Refresh-Path
    $grok = Find-Grok
    if (-not $grok) { Fail 'Grok Build could not be installed.' "Check your internet connection and run the installer again. To install it by hand, run in PowerShell:  irm $GrokInstallUrl | iex" }
    $env:GROK_BIN = $grok   # so install.ps1 finds it in this window
    $justInstalledGrok = $true
    Ok "Grok Build installed: $grok"
} elseif ($Manual) { Info 'Grok Build skipped (manual mode)' }
else { Ok 'Grok Build already installed, skipped'; $env:GROK_BIN = $grok }

# ---------- 4. dashboard setup + settings ----------
Step '4/5 Dashboard setup and settings'
$installArgs = @{}
if ($Manual) { $installArgs['Manual'] = $true }
$setupOk = $true
try { & (Join-Path $PSScriptRoot 'install.ps1') @installArgs; if ($LASTEXITCODE -ne 0) { $setupOk = $false } } catch { $setupOk = $false }
if (-not $setupOk -or -not (Test-Path -LiteralPath $venvPy)) {
    Fail 'the dashboard set-up step did not finish (see the messages above).' 'Check your internet connection and run the installer again. If it keeps failing, ask your Grok Bot to read the messages above.'
}
$env:PYTHONUTF8 = '1'
$pct = if ($Manual) { 'manual' } else { 'auto' }
$out = & $venvPy oneclick.py pct-source $pct
if ($LASTEXITCODE -ne 0) { Fail "usage_config.json could not be updated ($out)." 'Fix or delete usage_config.json in this folder, then run the installer again.' }
Ok "usage_config.json: reading is $pct"
if (-not $Manual -and -not $NoGrokAuto) {
    $out = & $venvPy oneclick.py grok-auto set
    if ($LASTEXITCODE -ne 0) {
        Info "Could not set Grok Build's default mode by itself: $out"
        Info 'To do it by hand: open .grok\config.toml in your user folder and put  permission_mode = "auto"  under the [ui] line.'
    } elseif ("$out" -match '"changed": true') { Ok "Grok Build's default mode is now 'auto' (saved, so it stays after you close Grok Build)" }
    elseif ("$out" -match '"note"') { Ok 'Grok Build already uses always-approve, which approves more than auto; left as it is' }
    else { Ok "Grok Build's default mode is already 'auto'" }
}

# ---------- 5. start ----------
Step '5/5 Desktop shortcut and starting the dashboard'
$shortcut = $null
if (-not $NoShortcut) {
    $shortcut = New-DesktopShortcut
    if ($shortcut) { Ok "Desktop shortcut: $shortcut" }
}
$url = $null
if (-not $NoStart) {
    $out = & $venvPy service.py start --config (Join-Path $PSScriptRoot 'usage_config.json') --open
    $code = $LASTEXITCODE
    try { $r = $out | ConvertFrom-Json } catch { $r = $null }
    if ($code -ne 0 -or -not $r -or -not $r.ok) {
        Fail 'the dashboard did not start.' 'Look at usage-data\service.log in this folder (or ask your Grok Bot to read it), then run the installer again.'
    }
    $url = $r.url
    if ($r.note -eq 'already running') { Ok 'Dashboard was already running (it keeps the version it started with; to load a newer one run stop.ps1, then start.ps1)' }
    else { Ok 'Dashboard is running' }
} else { Info 'Skipped (-NoStart). Start it later by double-clicking Install-Windows.cmd again, or with start.ps1.' }

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host ' Done.' -ForegroundColor Green
if ($url) { Write-Host " Your dashboard: $url" -ForegroundColor Green }
if ($shortcut) { Write-Host " Desktop shortcut: $shortcut   (delete it when you uninstall)" }
Write-Host ' Stop it with:   powershell -ExecutionPolicy Bypass -File .\stop.ps1'
# Sign-in hint only when sign-in looks missing. This only asks whether Grok Build's sign-in file EXISTS
# (Test-Path); the file is never opened or read.
$grokHome = if ($env:GROK_HOME) { $env:GROK_HOME } else { Join-Path $env:USERPROFILE '.grok' }
$signedIn = Test-Path -LiteralPath (Join-Path $grokHome 'auth.json') -PathType Leaf
if (-not $Manual -and $signedIn) {
    Write-Host ''
    Write-Host ' Grok Build: a sign-in was found on this PC, so the automatic reading should work.'
} elseif (-not $Manual -and $justInstalledGrok) {
    Write-Host ''
    Write-Host ' One last step: sign in to Grok Build once. Open a NEW PowerShell window and run  grok'
    Write-Host ' (a browser page opens; check that the account is yours, press Allow, then quit with Ctrl+Q).'
} elseif (-not $Manual) {
    Write-Host ''
    Write-Host ' No Grok Build sign-in found on this PC. Open a NEW PowerShell window and run  grok  once (a browser page'
    Write-Host ' opens; check that the account is yours, press Allow, then quit with Ctrl+Q). Until then the automatic reading shows as unavailable.'
    Write-Host ' (Already signed in? Then you can ignore this note.)'
}
Write-Host '============================================================' -ForegroundColor Green
exit 0
