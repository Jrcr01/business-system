@echo off
REM Slicks ERP (Django) on the SANDBOX database, http://127.0.0.1:8505 -- laptop testing only (never the live database).
cd /d "%~dp0"
set ERP_DB_PATH=%~dp0data\ap_disbursement_TEST.db
set ERP_DEBUG=1
set PYTHONIOENCODING=utf-8
"%~dp0.venv\Scripts\python.exe" erp\manage.py runserver 127.0.0.1:8505 --noreload
