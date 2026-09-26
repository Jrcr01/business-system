@echo off
REM Brings the LIVE database up to date with the program (adds any new tables/columns), then restarts the app.
REM Safe to run any time: it backs the database up first and does nothing if everything is already current.
cd /d "%~dp0"
set PY="C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe"

echo Turning the live app off...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }"
timeout /t 2 /nobreak >nul

echo.
echo Updating the database...
%PY% apply_pending_migrations.py
if errorlevel 1 goto :failed

echo.
echo Starting the live app...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
start "" http://localhost:8501
echo.
echo Done.
pause
exit /b 0

:failed
echo.
echo *** Something went wrong. Copy the text above and send it to Claude. ***
pause
exit /b 1
