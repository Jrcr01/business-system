# Stops the live app (whatever is listening on port 8501). Shared by stop_live.bat, update_database.bat and the one-time
# scripts, so the "turn the app off" step lives in one place (2026-09-27). The data is on disk and is not touched.
$c = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if ($c) { Stop-Process -Id ($c | Select-Object -First 1).OwningProcess -Force; Write-Host 'Live app stopped.' } else { Write-Host 'Live app was not running.' }
