#!/usr/bin/env bash
# Run ON THE LAPTOP (Git Bash) from C:\Slicks\Slicks_ERP:   deploy/deploy_to_server.sh slicks@test.slickstiresupply.com
# Sends the committed code (git archive of HEAD -- never uncommitted files, never data, never .venv) to the server over SSH and runs
# deploy/release.sh there. The server never needs GitHub access.
set -euo pipefail

# Use Windows' own SSH when it is there: it holds the key unlocked by ssh-add (ssh-agent service); Git Bash's ssh cannot see it.
if [ -x /c/Windows/System32/OpenSSH/ssh.exe ]; then SSH=/c/Windows/System32/OpenSSH/ssh.exe; SCP=/c/Windows/System32/OpenSSH/scp.exe
else SSH=ssh; SCP=scp; fi

TARGET="${1:?usage: deploy/deploy_to_server.sh slicks@<server>}"
cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain)" ]; then
  echo "Commit your changes first -- only committed code is deployed."
  exit 1
fi
ID="$(date +%Y%m%d_%H%M%S)_$(git rev-parse --short HEAD)"
echo "Deploying $(git log -1 --format='%h %s') as release $ID to $TARGET ..."
git archive --format=tar HEAD | "$SSH" "$TARGET" "mkdir -p /srv/slicks/app/releases/$ID && tar -x -C /srv/slicks/app/releases/$ID"
"$SSH" "$TARGET" "bash /srv/slicks/app/releases/$ID/deploy/release.sh $ID"
