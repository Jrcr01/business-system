@echo off
REM One-time: moves the live database out of Google Drive to C:\AP_Data (see move_database_out_of_drive.py).
REM Turns the live app off first, checks the copy is identical before switching, keeps the old file as a
REM safety net, then starts the app again.
cd /d "%~dp0"
set PY="C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe"

echo Turning the live app off...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }"
timeout /t 2 /nobreak >nul

echo.
echo ---- Preview (nothing is changed yet) ----
%PY% move_database_out_of_drive.py --dry-run
if errorlevel 1 goto :failed

echo.
set /p GO=Type YES and press Enter to move the database now (anything else cancels):
if /i not "%GO%"=="YES" goto :cancelled

echo.
%PY% move_database_out_of_drive.py
if errorlevel 1 goto :failed

echo.
echo Starting the live app from its new location...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
start "" http://localhost:8501
echo.
echo Done. Check that your data is all there, then send Claude a copy of this window.
pause
exit /b 0

:cancelled
echo Cancelled -- nothing was changed. Starting the app again...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
pause
exit /b 0

:failed
echo.
echo *** Something went wrong. Nothing was moved. Copy the text above and send it to Claude. ***
pause
exit /b 1
