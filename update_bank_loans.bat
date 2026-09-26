@echo off
REM One-time: writes bank names in full, puts creditor names in proper case, gives suppliers with no terms 120 days, adds the "Cancel bank loan" feature to the live database and deletes the duplicate PNB loan (id 24).
REM Turns the live app off, backs up, changes, then restarts the app.
cd /d "%~dp0"
set PY="C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe"

echo Turning the live app off...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }"
timeout /t 2 /nobreak >nul

echo.
echo ---- Step 1: update the database structure ----
%PY% apply_pending_migrations.py
if errorlevel 1 goto :failed

echo.
echo ---- Step 2: the loan that will be deleted ----
%PY% delete_duplicate_bank_loan.py --id 24 --dry-run
if errorlevel 2 goto :restart
if errorlevel 1 goto :failed
echo.
set /p GO=Type YES and press Enter to delete this loan (anything else skips the delete):
if /i not "%GO%"=="YES" goto :restart
%PY% delete_duplicate_bank_loan.py --id 24
if errorlevel 1 goto :failed

:restart
echo.
echo ---- Step 3: suppliers with no terms get the 120-day default ----
%PY% set_default_supplier_terms.py
if errorlevel 1 goto :failed

echo.
echo ---- Step 4: bank names become full names (BDO -> BDO Unibank, Inc., ...) ----
%PY% rename_banks_full_names.py
if errorlevel 1 goto :failed

echo.
echo ---- Step 5: creditor names back to proper case ----
%PY% restore_creditor_case.py
if errorlevel 1 goto :failed

echo.
echo Starting the live app...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
start "" http://localhost:8501
echo.
echo Done. Copy this window's text and send it to Claude to double-check.
pause
exit /b 0

:failed
echo.
echo *** Something went wrong. Copy the text above and send it to Claude. ***
pause
exit /b 1
