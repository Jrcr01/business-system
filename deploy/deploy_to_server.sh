#!/usr/bin/env bash
# Run ON THE LAPTOP (Git Bash) from C:\Slicks\Slicks_ERP:   deploy/deploy_to_server.sh slicks@<server> [--anyway]
# Sends the committed code (git archive of HEAD -- never uncommitted files, never data, never .venv) to the server over SSH and runs
# deploy/release.sh there. The server never needs GitHub access.
#
# Releasing while people work (owner, 2026-10-09 -- see deploy/README.md "The release routine"):
#   * a BIG change (a new migration, or the money / clearing / permission rules) goes live only between 12 AM and 6 AM Manila time;
#   * every deploy first checks who is active in the last 10 minutes and stops if anyone is;
#   --anyway skips both checks (an urgent fix the owner approved).
#   After the release: the commit is tagged live-<release id>, and the server log is scanned for errors since the restart.
set -euo pipefail

# Use Windows' own SSH when it is there: it holds the key unlocked by ssh-add (ssh-agent service); Git Bash's ssh cannot see it.
if [ -x /c/Windows/System32/OpenSSH/ssh.exe ]; then SSH=/c/Windows/System32/OpenSSH/ssh.exe; SCP=/c/Windows/System32/OpenSSH/scp.exe
else SSH=ssh; SCP=scp; fi
export MSYS_NO_PATHCONV=1                                   # keep /srv/... paths as they are when talking to the server

TARGET="${1:?usage: deploy/deploy_to_server.sh slicks@<server> [--anyway]}"
ANYWAY="${2:-}"
cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain)" ]; then
  echo "Commit your changes first -- only committed code is deployed."
  exit 1
fi

# 1. Big or small: what changed since the release that is live now
LAST="$(git tag --list 'live-*' --sort=-creatordate | head -1 || true)"
if [ -n "$LAST" ]; then CHANGED="$(git diff --name-only "$LAST"..HEAD)"; else CHANGED="$(git ls-files)"; fi
BIG="$(printf '%s\n' "$CHANGED" | grep -E '^(migrations/.*\.sql|schema\.sql|engines/(clearing|card_rules|cc_service|cc_rules|permissions|auth_service|statement_rules|check_rules|bank_statement_service|batch_statement_service|loan_schedule_service|bank_loan_service|reports_service|cash_insights_service|po_rules|payables_waterfall)\.py)$' || true)"
HOUR="$(( (10#$(date -u +%H) + 8) % 24 ))"     # Manila = UTC+8 (Git Bash on Windows has no Asia/Manila zone)
echo "Live now: ${LAST:-(no tag yet)}. Changed files: $(printf '%s\n' "$CHANGED" | grep -c . || true)."
if [ -n "$BIG" ]; then
  echo "BIG change (it touches):"; printf '  %s\n' $BIG
  if [ "$ANYWAY" != "--anyway" ] && [ "$HOUR" -ge 6 ]; then
    echo "Big changes go live between 12 AM and 6 AM Manila (it is ${HOUR}:00 now). Deploy then, or add --anyway for an urgent fix."
    exit 2
  fi
fi

# 2. Who is active right now
if [ "$ANYWAY" != "--anyway" ]; then
  set +e
  ACTIVE="$("$SSH" "$TARGET" "cd /srv/slicks/app/current && set -a && . /etc/slicks-erp/env && set +a && /srv/slicks/venv/bin/python web/manage.py who_is_active --minutes 10" 2>&1)"
  CODE=$?
  set -e
  echo "$ACTIVE"
  if [ "$CODE" = 3 ]; then
    echo "Someone is working in the system. Wait, or add --anyway (they may see a few seconds of 'try again')."
    exit 3
  fi
fi

# 3. The release
ID="$(date +%Y%m%d_%H%M%S)_$(git rev-parse --short HEAD)"
echo "Deploying $(git log -1 --format='%h %s') as release $ID to $TARGET ..."
STARTED="$(date -u '+%Y-%m-%d %H:%M:%S')"
git archive --format=tar HEAD | "$SSH" "$TARGET" "mkdir -p /srv/slicks/app/releases/$ID && tar -x -C /srv/slicks/app/releases/$ID"
"$SSH" "$TARGET" "bash /srv/slicks/app/releases/$ID/deploy/release.sh $ID"
git tag "live-$ID"
echo "Tagged live-$ID (what is live now)."

# 4. Errors since the restart
sleep 20
ERRORS="$("$SSH" "$TARGET" "journalctl -u slicks-erp --since '$STARTED UTC' --no-pager 2>/dev/null | grep -E '\" 5[0-9][0-9] |Traceback|ERROR' | grep -v 'Control server error' | tail -20" || true)"
if [ -n "$ERRORS" ]; then echo "Errors in the server log since the release:"; echo "$ERRORS"
else echo "No errors in the server log since the release."; fi
