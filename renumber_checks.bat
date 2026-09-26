@echo off
REM One-time: makes every existing check number the full 10-digit number (399 + the 7 digits).
REM Turns the live app off, shows a preview, asks before changing anything, backs up first, then restarts the app.
cd /d "%~dp0"
set PY="C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe"

echo Turning the live app off...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }"
timeout /t 2 /nobreak >nul

echo.
echo ---- Preview (nothing is changed yet) ----
%PY% prefix_existing_check_numbers.py --dry-run
if errorlevel 1 goto :failed

echo.
set /p GO=Type YES and press Enter to renumber the checks now (anything else cancels):
if /i not "%GO%"=="YES" goto :cancelled

echo.
%PY% prefix_existing_check_numbers.py
if errorlevel 1 goto :failed

echo.
echo Starting the live app with the latest changes...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
start "" http://localhost:8501
echo.
echo Done. Copy this window's text and send it to Claude to double-check.
pause
exit /b 0

:cancelled
echo Cancelled -- nothing was changed. Starting the app again...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
pause
exit /b 0

:failed
echo.
echo *** Something went wrong. Nothing was changed. Copy the text above and send it to Claude. ***
pause
exit /b 1
