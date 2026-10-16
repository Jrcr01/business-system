@echo off
REM One-time Google Drive sign-in for the thermal statement QR copies. Safe to run again.
cd /d "%~dp0"
call "%~dp0settings.bat"
%PY% drive_connect.py
echo.
pause
