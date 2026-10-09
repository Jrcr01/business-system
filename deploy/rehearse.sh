#!/usr/bin/env bash
# Run ON THE LAPTOP (Git Bash) from C:\Slicks\Slicks_ERP:   deploy/rehearse.sh slicks@<server>
# A rehearsal of a BIG change on a copy of the real data (owner, 2026-10-09): copies last night's live backup to data/rehearsal/ (never in git),
# brings it up to this code's schema with the same step the release uses, and prints the command for a sandbox on port 8508 on that copy
# (pick who to be, no password -- like the 8505 sandbox). Delete data/rehearsal/ afterwards.
set -euo pipefail
if [ -x /c/Windows/System32/OpenSSH/ssh.exe ]; then SSH=/c/Windows/System32/OpenSSH/ssh.exe; SCP=/c/Windows/System32/OpenSSH/scp.exe
else SSH=ssh; SCP=scp; fi
export MSYS_NO_PATHCONV=1
TARGET="${1:?usage: deploy/rehearse.sh slicks@<server>}"
cd "$(git rev-parse --show-toplevel)"
NEWEST="$("$SSH" "$TARGET" "ls -1t /srv/slicks/backups/slicks_erp_backup_*.db | head -1")"
[ -n "$NEWEST" ] || { echo "No nightly backup found on the server."; exit 1; }
mkdir -p data/rehearsal
COPY="data/rehearsal/REHEARSAL_TEST_$(basename "$NEWEST")"     # TEST in the name: the SANDBOX tag shows and it can never pass for the live database
"$SCP" "$TARGET:$NEWEST" "$COPY"
.venv/Scripts/python.exe apply_pending_migrations.py --db "$COPY"
echo
echo "Rehearsal copy ready: $COPY"
echo "Start it with:   WEB_DB_PATH=\"$(pwd)/$COPY\" WEB_DEBUG=1 WEB_SANDBOX_NO_SIGNIN=1 .venv/Scripts/python.exe web/manage.py runserver 127.0.0.1:8508 --noreload"
echo "When done:       rm -rf data/rehearsal"
