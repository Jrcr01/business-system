@echo off
call "%~dp0settings.bat"
set "AP_DB_PATH=%~dp0data\ap_disbursement_TEST.db"
%PY% -m streamlit run "%~dp0app.py" --server.headless true --server.port 8502
