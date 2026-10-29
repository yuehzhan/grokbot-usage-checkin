@echo off
rem Double-click this file to install and start the dashboard on Windows.
rem The execution policy is relaxed for this one PowerShell process only; nothing is changed system-wide.
rem pushd (not cd) so that a folder on a network drive works too.
pushd "%~dp0"
if not exist "one-click-install.ps1" (
  echo.
  echo Problem: one-click-install.ps1 was not found next to this file.
  echo What to do: unzip the whole download first ^(right-click the ZIP, "Extract All"^), then double-click this file inside the unzipped folder.
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File ".\one-click-install.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
pause
popd
exit /b %RC%
