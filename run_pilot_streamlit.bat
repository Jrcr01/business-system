@echo off
call "%~dp0settings.bat"
REM PILOT COPY (2026-10-03). A clean database of its own, with the SIGN-IN ON, for trying the whole system with the real people before anything is deployed.
REM   * The first time, it builds data\ap_disbursement_PILOT_TEST.db from schema.sql and asks you to create the System admin account.
REM   * The name contains TEST on purpose: the screen shows the orange SANDBOX banner and live AP data can never be mixed in.
REM   * It is not the live AP and not the test copy of live data (run_test_streamlit.bat). Delete the PILOT_TEST file to start over.
REM   * Only this laptop can open it (127.0.0.1). To let the brother try it on the same Wi-Fi, change 127.0.0.1 to 0.0.0.0 below, and tell him http://<this laptop's address>:8506 .
set "AP_DB_PATH=%~dp0data\ap_disbursement_PILOT_TEST.db"
if not exist "%AP_DB_PATH%" (
  echo Building a clean pilot database...
  %PY% -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.executescript(open(sys.argv[2],encoding='utf-8').read()); c.commit()" "%AP_DB_PATH%" "%~dp0schema.sql"
)
%PY% -m streamlit run "%~dp0app.py" --server.headless true --server.port 8506 --server.address 127.0.0.1
