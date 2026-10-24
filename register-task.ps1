<#
.SYNOPSIS
  OPTIONAL. Starts the page + sampler automatically when you sign in to Windows.
  Nothing is registered unless you run this script. Undo with -Uninstall.

.DESCRIPTION
  Registers a per-user Task Scheduler task "GrokBotUsageCheckin" (at logon,
  runs hidden with pythonw.exe, no time limit). If Windows refuses to create the
  task without administrator rights, it falls back to a shortcut in your Startup
  folder, which needs no rights. -Uninstall removes both.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\register-task.ps1
  powershell -ExecutionPolicy Bypass -File .\register-task.ps1 -Uninstall
#>
param(
    [switch]$Uninstall,
    # Skip Task Scheduler and only use the Startup-folder shortcut.
    [switch]$StartupShortcut
)
$ErrorActionPreference = 'Stop'
$TaskName = 'GrokBotUsageCheckin'
$Startup = [Environment]::GetFolderPath('Startup')
$Lnk = Join-Path $Startup 'Grok Bot Usage & Check-in.lnk'

if ($Uninstall) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task $TaskName."
    }
    if (Test-Path -LiteralPath $Lnk) {
        Remove-Item -LiteralPath $Lnk
        Write-Host 'Removed Startup shortcut.'
    }
    if (-not $t -and -not (Test-Path -LiteralPath $Lnk)) { Write-Host 'Nothing was registered.' }
    Write-Host 'The page keeps running until you run stop.ps1 or sign out.'
    exit 0
}

$pyw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pyw)) {
    Write-Host 'Not installed yet. Run install.ps1 first.' -ForegroundColor Red
    exit 1
}
$svc = Join-Path $PSScriptRoot 'service.py'
$cfg = Join-Path $PSScriptRoot 'usage_config.json'
# "run" keeps a small supervisor alive for the whole session and restarts the
# page/sampler if they crash. It exits at once if start.ps1 already started one.
$argLine = '"' + $svc + '" run --config "' + $cfg + '"'

$done = $false
if (-not $StartupShortcut) {
    try {
        $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $action = New-ScheduledTaskAction -Execute $pyw -Argument $argLine -WorkingDirectory $PSScriptRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Description 'Grok Bot Usage & Check-in: local page + hourly sampler' -Force | Out-Null
        Write-Host "Registered scheduled task $TaskName (runs at sign-in)." -ForegroundColor Green
        $done = $true
    } catch {
        Write-Host "Task Scheduler refused ($($_.Exception.Message.Trim())). Using a Startup shortcut instead." -ForegroundColor Yellow
    }
}
if (-not $done) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($Lnk)
    $s.TargetPath = $pyw
    $s.Arguments = $argLine
    $s.WorkingDirectory = $PSScriptRoot
    $s.WindowStyle = 7
    $s.Description = 'Grok Bot Usage & Check-in'
    $s.Save()
    Write-Host "Created Startup shortcut: $Lnk" -ForegroundColor Green
}
Write-Host 'It starts at your next sign-in. To start it now, run start.ps1.'
Write-Host 'Undo:  powershell -ExecutionPolicy Bypass -File .\register-task.ps1 -Uninstall'
