#!/usr/bin/env bash
# Runs ON THE SERVER as slicks, called by deploy_to_server.sh after it unpacked a release into /srv/slicks/app/releases/<id>.
# Installs the pinned packages, brings the database up to schema.sql (adds what is missing, never changes or removes anything; a copy is
# made first), collects the static files, checks the security settings, switches "current" to the new release and restarts BOTH programs
# (the Django system and the Streamlit screens). If the system does not answer after the restart, it switches back to the previous release.
set -euo pipefail

ID="${1:?release id}"
APP=/srv/slicks/app
NEW="$APP/releases/$ID"
PY=/srv/slicks/venv/bin/python
set -a; . /etc/slicks-erp/env; set +a

cd "$NEW"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements-server.txt
if [ -f "$WEB_DB_PATH" ]; then
  "$PY" -c "from db_location import safe_copy; import sys; safe_copy(sys.argv[1], sys.argv[2])" "$WEB_DB_PATH" "/srv/slicks/backups/before_release_$ID.db"
  "$PY" apply_pending_migrations.py --db "$WEB_DB_PATH"
  "$PY" web/manage.py fill_usernames                      # sign in with a username (2026-10-08): accounts without one get it from their email; a no-op once done
  "$PY" web/manage.py rekey_items --apply                 # item keys with every spec (2026-10-08); lists possible duplicates, never merges; a no-op once done
else
  echo "No database yet at $WEB_DB_PATH -- upload it first (deploy/upload_data.sh), then deploy again."
fi
"$PY" web/manage.py collectstatic --noinput -v 0
"$PY" web/manage.py check --deploy --fail-level WARNING

PREV="$(readlink -f "$APP/current" 2>/dev/null || true)"
ln -sfn "$NEW" "$APP/current.tmp" && mv -Tf "$APP/current.tmp" "$APP/current"
sudo /usr/bin/systemctl restart slicks-erp
sudo /usr/bin/systemctl restart slicks-streamlit

HOST="${WEB_ALLOWED_HOSTS%%,*}"
code=000
for i in 1 2 3 4 5 6 7 8 9 10; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" -H "X-Forwarded-Proto: https" http://127.0.0.1:8000/health/ || true)"
  [ "$code" = 200 ] && { echo "Release $ID is live (the system answers)."; break; }
  sleep 2
done
if [ "$code" != 200 ]; then
  echo "The system did not answer after the restart (HTTP $code)."
  if [ -n "$PREV" ] && [ "$PREV" != "$NEW" ]; then
    ln -sfn "$PREV" "$APP/current.tmp" && mv -Tf "$APP/current.tmp" "$APP/current"
    sudo /usr/bin/systemctl restart slicks-erp
    sudo /usr/bin/systemctl restart slicks-streamlit
    echo "Switched back to the previous release: $(basename "$PREV")."
  fi
  exit 1
fi
# keep the newest 5 releases and the newest 10 pre-release database copies
ls -1dt "$APP"/releases/* | tail -n +6 | xargs -r rm -rf
ls -1t /srv/slicks/backups/before_release_*.db 2>/dev/null | tail -n +11 | xargs -r rm -f
