@echo off
REM Slick's on DJANGO, sandbox (stage D0, 2026-10-03): http://127.0.0.1:8505 . Laptop testing only: never the live database.
REM   * The first time, it builds data\ap_disbursement_WEB_TEST.db from schema.sql and the site asks you to create the System admin account.
REM   * The name contains TEST on purpose: the orange SANDBOX tag shows and live AP data can never be mixed in.
REM   * Delete the WEB_TEST file to start over. The Streamlit pilot (run_pilot_streamlit.bat, 8506) has its own database and is not touched.
cd /d "%~dp0"
set "WEB_DB_PATH=%~dp0data\ap_disbursement_WEB_TEST.db"
set WEB_DEBUG=1
REM No password and no 6-digit code here (owner, 2026-10-08): Sign in shows each person as a button. The live server always asks for both.
set WEB_SANDBOX_NO_SIGNIN=1
set PYTHONIOENCODING=utf-8
if not exist "%WEB_DB_PATH%" (
  echo Building a clean sandbox database...
  "%~dp0.venv\Scripts\python.exe" -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.executescript(open(sys.argv[2],encoding='utf-8').read()); c.commit()" "%WEB_DB_PATH%" "%~dp0schema.sql"
)
"%~dp0.venv\Scripts\python.exe" web\manage.py runserver 127.0.0.1:8505 --noreload
