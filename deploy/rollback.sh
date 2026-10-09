#!/usr/bin/env bash
# Run ON THE LAPTOP (Git Bash):   deploy/rollback.sh slicks@<server>
# Puts the release before the current one back (owner, 2026-10-09: one command if an update goes wrong). Only the code changes: migrations only ever
# ADD, so the older code runs on the same database and nothing typed since the update is lost. (Restoring the database from a before_release_*
# backup is a different, last-resort step -- it would undo the entries made since -- and is never done without the owner's OK.)
set -euo pipefail
if [ -x /c/Windows/System32/OpenSSH/ssh.exe ]; then SSH=/c/Windows/System32/OpenSSH/ssh.exe; else SSH=ssh; fi
export MSYS_NO_PATHCONV=1
TARGET="${1:?usage: deploy/rollback.sh slicks@<server>}"
"$SSH" "$TARGET" 'set -e
APP=/srv/slicks/app
CUR="$(basename "$(readlink -f "$APP/current")")"
PREV="$(ls -1 "$APP/releases" | sort | grep -B1 -x "$CUR" | head -1)"
if [ -z "$PREV" ] || [ "$PREV" = "$CUR" ]; then echo "No earlier release to go back to (current: $CUR)."; exit 1; fi
ln -sfn "$APP/releases/$PREV" "$APP/current.tmp" && mv -Tf "$APP/current.tmp" "$APP/current"
sudo /usr/bin/systemctl restart slicks-erp
sleep 4
code="$(curl -s -o /dev/null -w "%{http_code}" -H "Host: erp.slickstiresupply.com" -H "X-Forwarded-Proto: https" http://127.0.0.1:8000/health/ || true)"
echo "Rolled back: $CUR -> $PREV (the system answers: HTTP $code)."'
echo "Tell me (or run deploy_to_server.sh again) to go forward once it is fixed. The git tag of the bad release stays; the next deploy tags anew."
