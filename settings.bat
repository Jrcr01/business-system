@echo off
REM ONE place for the machine-specific settings every .bat file here uses (2026-09-27). Every .bat calls this first.
REM Folders are worked out from where the files sit, so moving the whole project folder needs no change here.
REM On another PC: put your own values in local_settings.bat next to this file (it is not in git and is read last,
REM so it wins), e.g.   set PY="C:\Python312\python.exe"
set PY="C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe"
set PYTHONIOENCODING=utf-8
if exist "%~dp0local_settings.bat" call "%~dp0local_settings.bat"
