@echo off
REM Backs up the live database (see backup_live_db.py). Safe to run any time, even while the app is open.
cd /d "%~dp0"
"C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe" backup_live_db.py
if not "%1"=="quiet" pause
