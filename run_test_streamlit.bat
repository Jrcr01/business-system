@echo off
call "%~dp0settings.bat"
set "AP_DB_PATH=%~dp0data\ap_disbursement_TEST.db"
REM Sign-in is skipped on this test copy only (2026-10-02, owner: "bypass the login page"). Delete the next line to ask for the sign-in again. Ignored on any database not named _TEST.
set "AP_SKIP_LOGIN=1"
REM --server.address 127.0.0.1 (2026-09-28): only this laptop can open the test copy -- it holds a copy of the real data.
%PY% -m streamlit run "%~dp0app.py" --server.headless true --server.port 8502 --server.address 127.0.0.1
