#!/usr/bin/env bash
# Run ON THE LAPTOP (Git Bash) from C:\Slicks\Slicks_ERP:   deploy/upload_data.sh slicks@erp.slickstiresupply.com <database file on this laptop>
# Puts a database on the server, as the file the server is set to use (/etc/slicks-erp/env WEB_DB_PATH): a sandbox copy for a rehearsal, or
# the live AP data at the move (the cutover, only with the owner's go-ahead and a backup taken first). A consistent copy is made with SQLite's
# backup API, so it is safe even while the laptop's app has the file open. The audit seal key / state files next to it go along.
# If the server already has a database there, nothing is replaced unless you type "yes" when asked (the old one is kept as a copy first).
set -euo pipefail

if [ -x /c/Windows/System32/OpenSSH/ssh.exe ]; then SSH=/c/Windows/System32/OpenSSH/ssh.exe; SCP=/c/Windows/System32/OpenSSH/scp.exe
else SSH=ssh; SCP=scp; fi

TARGET="${1:?usage: deploy/upload_data.sh slicks@<server> <database file>}"
SRC="${2:?usage: deploy/upload_data.sh slicks@<server> <database file>}"
[ -f "$SRC" ] || { echo "No file $SRC"; exit 1; }
cd "$(git rev-parse --show-toplevel)"

DEST="$("$SSH" "$TARGET" 'set -a; . /etc/slicks-erp/env; echo "$WEB_DB_PATH"')"
NAME="$(basename "$DEST")"
if "$SSH" "$TARGET" "test -f '$DEST'"; then
  echo "The server already has a database at $DEST."
  read -r -p "Replace it with $SRC? (the old one is kept as a copy) Type yes: " ANSWER
  [ "$ANSWER" = "yes" ] || { echo "Nothing changed."; exit 1; }
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python -c "import sys; from db_location import safe_copy; safe_copy(sys.argv[1], sys.argv[2])" "$SRC" "$TMP/$NAME"
DIR="$(dirname "$SRC")"
for f in audit_seal.key audit_seal.state; do [ -f "$DIR/$f" ] && cp "$DIR/$f" "$TMP/"; done
echo "Uploading $SRC ($(du -k "$TMP/$NAME" | cut -f1) KB) to $TARGET:$DEST ..."
"$SSH" "$TARGET" "sudo /usr/bin/systemctl stop slicks-erp; sudo /usr/bin/systemctl stop slicks-streamlit; \
  if [ -f '$DEST' ]; then cp -p '$DEST' /srv/slicks/backups/replaced_\$(date +%Y%m%d_%H%M%S)_$NAME; fi"
LOCAL=("$TMP"/*)
command -v cygpath >/dev/null && for i in "${!LOCAL[@]}"; do LOCAL[$i]="$(cygpath -w "${LOCAL[$i]}")"; done
MSYS_NO_PATHCONV=1 "$SCP" -q "${LOCAL[@]}" "$TARGET:/srv/slicks/data/"
"$SSH" "$TARGET" "rm -f '$DEST-wal' '$DEST-shm'; sudo /usr/bin/systemctl start slicks-erp; sudo /usr/bin/systemctl start slicks-streamlit"
echo "Done. Now deploy (it brings the database up to the current schema, with a copy first): deploy/deploy_to_server.sh $TARGET"
