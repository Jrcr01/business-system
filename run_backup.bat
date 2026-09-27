@echo off
REM Backs up the live database (see backup_live_db.py). Safe to run any time, even while the app is open.
cd /d "%~dp0"
call "%~dp0settings.bat"
%PY% backup_live_db.py
REM Then switch off statement QR links that are past their date (never stops the backup)
%PY% expire_statement_links.py
if not "%1"=="quiet" pause
