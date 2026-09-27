# One-command restart for the live app (2026-09-17) -- picks up whatever code/schema changes
# have landed since it was last started. Stops whatever's currently listening on 8501 (the live
# port), then relaunches run_live_streamlit.bat and waits for it to come back up.
#
# Run this after: any code change to app.py/engines/*.py, or after applying a schema change via
# apply_pending_migrations.py. Not needed for data entered through the app itself -- that's live
# immediately, no restart involved.

$ProjectRoot = $PSScriptRoot   # the folder this script sits in
$Port = 8501

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Stopping current live app (PID $($existing.OwningProcess))..."
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
} else {
    Write-Host "No live app currently running on port $Port."
}

Write-Host "Starting live app..."
Start-Process -FilePath "$ProjectRoot\run_live_streamlit.bat" -WorkingDirectory $ProjectRoot -WindowStyle Hidden

$found = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { $found = $true; break }
}

if ($found) {
    Write-Host "Live app is back up: http://localhost:$Port (PID $($conn.OwningProcess))"
} else {
    Write-Host "Port $Port never came up after 30s -- something went wrong. Check for a startup error (e.g. run run_live_streamlit.bat directly to see it)."
}
