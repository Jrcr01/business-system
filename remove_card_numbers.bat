@echo off
REM One-time (2026-09-27): removes full credit card numbers -- from the live database and from every old copy
REM (old databases and backups on Google Drive and on this laptop, TblCreditCard.txt, Cards_to_verify.md). Only the
REM last 4 digits are kept, which is all the app uses. Turns the live app off, shows a preview, asks before changing
REM anything, then restarts the app (the restart also applies the "only this laptop can open the app" setting).
cd /d "%~dp0"
set PY="C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe"
set PYTHONIOENCODING=utf-8

echo Turning the live app off...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }"
timeout /t 2 /nobreak >nul

echo.
echo ---- Preview: live database (nothing is changed yet) ----
%PY% remove_full_card_numbers.py
if errorlevel 1 goto :failed
echo.
echo ---- Preview: old copies (nothing is changed yet) ----
%PY% clean_card_numbers_in_copies.py
if errorlevel 1 goto :failed

echo.
set /p GO=Type YES and press Enter to remove the full card numbers now (anything else cancels):
if /i not "%GO%"=="YES" goto :cancelled

echo.
echo ---- Live database ----
%PY% remove_full_card_numbers.py --apply
if errorlevel 1 goto :failed
echo.
echo ---- Old copies (this also cleans the local backup made by the step above) ----
%PY% clean_card_numbers_in_copies.py --apply
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
