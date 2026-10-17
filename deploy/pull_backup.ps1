# Slick's ERP -- the off-site copy (owner, 2026-10-15, architecture review: every backup was on the same disk as the live data).
# Pulls the newest nightly backup, the secrets beside the database and the bank statement files from the server into C:\Slicks\Backups\server,
# keeping the newest 30 database copies. Runs every evening from the laptop (Task Scheduler: "Slicks ERP backup pull", 7:30 PM, after the
# server's 6:00 PM nightly backup). Needs only the SSH key the deploys already use (~/.ssh/slicks_erp).
$ErrorActionPreference = "Stop"
$server = "slicks@165.245.188.246"
$key = "$env:USERPROFILE\.ssh\slicks_erp"
$dest = "C:\Slicks\Backups\server"
$log = "$dest\pull.log"
New-Item -ItemType Directory -Force "$dest\db", "$dest\secrets", "$dest\bank" | Out-Null
function Say($t) { $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm"), $t; Add-Content -Path $log -Value $line -Encoding utf8; Write-Output $line }
try {
    $newest = (& ssh -i $key -o BatchMode=yes -o ConnectTimeout=20 $server "ls -t /srv/slicks/backups/slicks_erp_backup_*.db | head -1").Trim()
    if (-not $newest) { throw "no nightly backup found on the server" }
    $name = Split-Path $newest -Leaf
    if (-not (Test-Path "$dest\db\$name")) {
        & scp -i $key -o BatchMode=yes -q "${server}:$newest" "$dest\db\$name"
        Say "pulled $name ($([math]::Round((Get-Item "$dest\db\$name").Length / 1KB)) KB)"
    } else { Say "already have $name" }
    foreach ($f in @("audit_seal.key", "audit_seal.state", "push_keys.json", "mail_out.json")) { & scp -i $key -o BatchMode=yes -q "${server}:/srv/slicks/data/$f" "$dest\secrets\$f" }
    & scp -i $key -o BatchMode=yes -q "${server}:/etc/slicks-erp/env" "$dest\secrets\env"
    & scp -i $key -o BatchMode=yes -q -r "${server}:/srv/slicks/backups/bank/." "$dest\bank\"
    if (-not (Test-Path "$dest\photos")) { New-Item -ItemType Directory -Force "$dest\photos" | Out-Null }
    & scp -i $key -o BatchMode=yes -q -r "${server}:/srv/slicks/backups/photos/." "$dest\photos\"      # the kept photos (2026-10-17)
    Get-ChildItem "$dest\db\slicks_erp_backup_*.db" | Sort-Object Name -Descending | Select-Object -Skip 30 | ForEach-Object { Remove-Item $_.FullName; Say "removed old $($_.Name)" }
    Say "done"
} catch {
    Say "FAILED: $($_.Exception.Message)"
    exit 1
}
