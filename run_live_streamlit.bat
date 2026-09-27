@echo off
REM Live instance. Uses data\ap_disbursement.db unless live_db_path.txt exists (written by
REM move_database_out_of_drive.py), in which case the database at the path on its first line is used.
REM Port 8501, matching .claude\launch.json's "ap-disbursement" config.
REM --server.address 127.0.0.1 (2026-09-27): only this laptop can open the app -- other devices on the same Wi-Fi cannot.
call "%~dp0settings.bat"
if exist "%~dp0live_db_path.txt" set /p AP_DB_PATH=<"%~dp0live_db_path.txt"
%PY% -m streamlit run "%~dp0app.py" --server.headless true --server.port 8501 --server.address 127.0.0.1
