@echo off
call "%~dp0settings.bat"
set "AP_DB_PATH=%~dp0data\ap_disbursement_TEST.db"
REM --server.address 127.0.0.1 (2026-09-28): only this laptop can open the test copy -- it holds a copy of the real data.
%PY% -m streamlit run "%~dp0app.py" --server.headless true --server.port 8502 --server.address 127.0.0.1
