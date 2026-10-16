@echo off
REM Double-click to turn the live app on, then open http://localhost:8501 in your browser.
REM (Same as restart_live.bat: starts it if it's off, restarts it if it's already on.)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_live.ps1"
start "" http://localhost:8501
pause
