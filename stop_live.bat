@echo off
REM Double-click to turn the live app off. Only stops the program listening on port 8501 --
REM your data (data\ap_disbursement.db) is saved on disk already and is not touched.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }"
pause
