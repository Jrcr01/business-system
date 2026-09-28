#!/usr/bin/env bash
# Run ON THE LAPTOP (Git Bash) from C:\Slicks\Slicks_ERP:   deploy/upload_test_data.sh slicks@test.slickstiresupply.com
# TEST SITE ONLY: copies the laptop SANDBOX database (data/ap_disbursement_TEST.db, a consistent copy made with SQLite's backup API)
# and its audit seal key / state files to the server, replacing the test site's data. Never the live database.
set -euo pipefail

# Use Windows' own SSH when it is there: it holds the key unlocked by ssh-add (ssh-agent service); Git Bash's ssh cannot see it.
if [ -x /c/Windows/System32/OpenSSH/ssh.exe ]; then SSH=/c/Windows/System32/OpenSSH/ssh.exe; SCP=/c/Windows/System32/OpenSSH/scp.exe
else SSH=ssh; SCP=scp; fi

TARGET="${1:?usage: deploy/upload_test_data.sh slicks@<server>}"
cd "$(git rev-parse --show-toplevel)"
SRC=data/ap_disbursement_TEST.db
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python -c "from db_location import safe_copy; safe_copy('$SRC', r'$TMP/ap_disbursement_TEST.db')"
for f in audit_seal.key audit_seal.state; do [ -f "data/$f" ] && cp "data/$f" "$TMP/"; done
echo "Uploading the sandbox copy ($(du -k "$TMP/ap_disbursement_TEST.db" | cut -f1) KB) to $TARGET ..."
"$SSH" "$TARGET" "sudo /usr/bin/systemctl stop slicks-erp"
# Windows paths for Windows' scp, and no automatic path rewriting by Git Bash (it would mangle "host:/srv/...")
LOCAL=("$TMP"/*)
command -v cygpath >/dev/null && for i in "${!LOCAL[@]}"; do LOCAL[$i]="$(cygpath -w "${LOCAL[$i]}")"; done
MSYS_NO_PATHCONV=1 "$SCP" -q "${LOCAL[@]}" "$TARGET:/srv/slicks/data/"
"$SSH" "$TARGET" "rm -f /srv/slicks/data/ap_disbursement_TEST.db-wal /srv/slicks/data/ap_disbursement_TEST.db-shm; sudo /usr/bin/systemctl start slicks-erp"
echo "Done. Deploy again if the ERP's database changes are newer than the copy: deploy/deploy_to_server.sh $TARGET"
