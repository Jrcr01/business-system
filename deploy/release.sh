#!/usr/bin/env bash
# Runs ON THE SERVER as slicks, called by deploy_to_server.sh after it unpacked a release into /srv/slicks/app/releases/<id>.
# Installs the pinned packages, updates the database (Django migrations only -- the test site's AP tables come with the data copy),
# collects the static files, checks the security settings, switches "current" to the new release and restarts. If the ERP does not
# answer after the restart, it switches back to the previous release.
set -euo pipefail

ID="${1:?release id}"
APP=/srv/slicks/app
NEW="$APP/releases/$ID"
PY=/srv/slicks/venv/bin/python
set -a; . /etc/slicks-erp/env; set +a

cd "$NEW"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements-server.txt
if [ -f "$ERP_DB_PATH" ]; then
  "$PY" erp/manage.py migrate --noinput
else
  echo "No database yet at $ERP_DB_PATH -- run deploy/upload_test_data.sh from the laptop, then deploy again."
fi
"$PY" erp/manage.py collectstatic --noinput -v 0
"$PY" erp/manage.py check --deploy --fail-level WARNING

PREV="$(readlink -f "$APP/current" 2>/dev/null || true)"
ln -sfn "$NEW" "$APP/current.tmp" && mv -Tf "$APP/current.tmp" "$APP/current"
sudo /usr/bin/systemctl restart slicks-erp

HOST="${ERP_ALLOWED_HOSTS%%,*}"
for i in 1 2 3 4 5 6 7 8 9 10; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" -H "X-Forwarded-Proto: https" http://127.0.0.1:8000/sign-in/ || true)"
  [ "$code" = 200 ] && { echo "Release $ID is live (sign-in page answers)."; break; }
  sleep 2
done
if [ "$code" != 200 ]; then
  echo "The ERP did not answer after the restart (HTTP $code)."
  if [ -n "$PREV" ] && [ "$PREV" != "$NEW" ]; then
    ln -sfn "$PREV" "$APP/current.tmp" && mv -Tf "$APP/current.tmp" "$APP/current"
    sudo /usr/bin/systemctl restart slicks-erp
    echo "Switched back to the previous release: $(basename "$PREV")."
  fi
  exit 1
fi
# keep the newest 5 releases
ls -1dt "$APP"/releases/* | tail -n +6 | xargs -r rm -rf
