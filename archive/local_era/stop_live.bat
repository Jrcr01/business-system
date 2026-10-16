@echo off
REM Double-click to turn the live app off. Only stops the program listening on port 8501 --
REM your data (data\ap_disbursement.db) is saved on disk already and is not touched.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_live.ps1"
pause
