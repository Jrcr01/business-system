@echo off
REM One-click: brings the LIVE database up to date with the latest changes, then restarts the app.
REM Order matters: schema first, then the statement history, then the two data fixes.
REM Every step makes its own backup first (files named ap_disbursement.db.bak_...). Safe to run twice.
cd /d "%~dp0"
set PY="C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe"

echo ================================================================
echo  Step 0: turning the live app off (if it is running)
echo ================================================================
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }"

echo.
echo ================================================================
echo  Step 1 of 4: update the database structure
echo ================================================================
%PY% apply_pending_migrations.py
if errorlevel 1 goto :failed

echo.
echo ================================================================
echo  Step 2 of 4: load last 3 months of past credit card statements
echo ================================================================
%PY% phase8e_backfill_recent_cc_statements.py
if errorlevel 1 goto :failed

echo.
echo ================================================================
echo  Step 3 of 4: move the AMEX card under BDO
echo ================================================================
%PY% fix_amex_issuer.py
if errorlevel 1 goto :failed

echo.
echo ================================================================
echo  Step 4 of 4: remove the 6 inactive credit cards
echo ================================================================
%PY% remove_inactive_cards.py
if errorlevel 1 goto :failed

echo.
echo ================================================================
echo  All 4 steps finished. Starting the live app...
echo ================================================================
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
start "" http://localhost:8501
echo.
echo Done. Take a screenshot of this window or copy its text and send it to Claude to double-check.
pause
exit /b 0

:failed
echo.
echo *** A step reported a problem. Nothing after it was run. ***
echo Copy the text above and send it to Claude before doing anything else.
pause
exit /b 1
