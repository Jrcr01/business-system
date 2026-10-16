@echo off
REM Monthly restore test: proves the newest Google Drive backup can rebuild the system. Read-only for the live
REM database and the backups (works on a temporary copy), so it is safe while the app is running.
cd /d "%~dp0"
call "%~dp0settings.bat"
%PY% restore_test.py 2>nul
echo.
echo Copy this window's text and send it to Claude if it says FAILED.
pause
