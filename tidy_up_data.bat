@echo off
REM One-time data tidy-up (2026-09-27): deletes the 3 completely empty draft order statements and creates the repayment
REM schedule of loan note PENDING-126 (Emelita Roxas) from its check, moves loan PN8004 7053 5001 8969 under PNB and
REM renames Honda Giorno to Inhouse Financing. Turns the live app off, shows a preview, asks
REM before changing anything, backs up first, then restarts the app.
cd /d "%~dp0"
set PY="C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe"
set PYTHONIOENCODING=utf-8

echo Turning the live app off...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }"
timeout /t 2 /nobreak >nul

echo.
echo ---- Preview (nothing is changed yet) ----
%PY% tidy_up_data.py
if errorlevel 1 goto :failed

echo.
set /p GO=Type YES and press Enter to apply the tidy-up now (anything else cancels):
if /i not "%GO%"=="YES" goto :cancelled

echo.
%PY% tidy_up_data.py --apply
if errorlevel 1 goto :failed

echo.
echo Starting the live app...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
start "" http://localhost:8501
echo.
echo Done. Copy this window's text and send it to Claude to double-check.
pause
exit /b 0

:cancelled
echo Cancelled - nothing was changed. Starting the live app again...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
pause
exit /b 0

:failed
echo.
echo Something went wrong - see the message above. Starting the live app again...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
pause
exit /b 1
