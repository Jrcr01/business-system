@echo off
REM JRCR's Business System, sandbox (2026-10-20, duplicated from Slick's ERP): http://127.0.0.1:8512 . Laptop testing only.
REM   * The first time, it builds data\business_system_TEST.db from schema.sql and the site asks you to create the System admin account.
REM   * The name contains TEST on purpose: the orange SANDBOX tag shows and live data can never be mixed in.
REM   * Delete the TEST file to start over.
cd /d "%~dp0"
set "WEB_DB_PATH=%~dp0data\business_system_TEST.db"
set WEB_DEBUG=1
REM No password and no 6-digit code here: Sign in shows each person as a button. A live server always asks for both.
set WEB_SANDBOX_NO_SIGNIN=1
set PYTHONIOENCODING=utf-8
if not exist "%WEB_DB_PATH%" (
  echo Building a clean sandbox database...
  "%~dp0.venv\Scripts\python.exe" -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.executescript(open(sys.argv[2],encoding='utf-8').read()); c.commit()" "%WEB_DB_PATH%" "%~dp0schema.sql"
)
"%~dp0.venv\Scripts\python.exe" web\manage.py runserver 127.0.0.1:8512 --noreload
