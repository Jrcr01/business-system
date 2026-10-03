#!/usr/bin/env bash
# Slick's system -- one-time setup of a fresh Ubuntu 24.04 server (DigitalOcean, Singapore). Server step F3 (2026-09-28), updated for the go-live
# side by side (2026-10-03): the Django system and the Streamlit screens not moved yet, on one database.
# Run ONCE as root on the new server:   bash bootstrap_server.sh erp.slickstiresupply.com live      (or "test" for a rehearsal with a sandbox copy)
# What it does (and nothing else):
#   * user "slicks" that runs the ERP and receives deploys (key sign-in only; the same key as root's first login)
#   * SSH: keys only, no root login, no passwords; firewall: only SSH, HTTP (80, for the certificate) and HTTPS (443)
#   * fail2ban (blocks repeated wrong SSH sign-ins), automatic security updates
#   * Python 3.12 environment, Tesseract (reading invoice photos), Caddy (HTTPS with an automatic certificate), the two services and the nightly check + backup
#   * /etc/slicks-erp/env with a new random secret key (readable by root and the ERP only; never in git)
# Safe to run again: every step checks what is already there.
set -euo pipefail

SITE="${1:?usage: bash bootstrap_server.sh <site name, e.g. erp.slickstiresupply.com> <live|test>}"
MODE="${2:?say live or test}"
case "$MODE" in
  live) DBFILE=ap_disbursement.db; LIVE=1 ;;
  test) DBFILE=ap_disbursement_TEST.db; LIVE=0 ;;
  *) echo "The second word must be live or test."; exit 1 ;;
esac
HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
grep -q 'VERSION_ID="24.04"' /etc/os-release || echo "Warning: written for Ubuntu 24.04."

echo "== swap file (2 GB): the 1 GB server slows down for a moment instead of stopping a program when memory runs short (owner chose 1 GB, 2026-10-03)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/90-slicks-swap.conf
fi

echo "== packages and automatic security updates"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv python3-dev sqlite3 ufw fail2ban unattended-upgrades tesseract-ocr \
    debian-keyring debian-archive-keyring apt-transport-https curl gnupg
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== Caddy (official package)"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

echo "== user slicks and folders"
id slicks >/dev/null 2>&1 || adduser --disabled-password --gecos "Slicks ERP" slicks
install -d -o slicks -g slicks -m 750 /srv/slicks /srv/slicks/app /srv/slicks/app/releases /srv/slicks/data /srv/slicks/backups /srv/slicks/static
install -d -o slicks -g slicks -m 700 /home/slicks/.ssh
if [ -f /root/.ssh/authorized_keys ] && [ ! -s /home/slicks/.ssh/authorized_keys ]; then
  install -o slicks -g slicks -m 600 /root/.ssh/authorized_keys /home/slicks/.ssh/authorized_keys
fi
[ -s /home/slicks/.ssh/authorized_keys ] || { echo "No SSH key for slicks -- add the laptop's public key first."; exit 1; }
# the deploy may restart the ERP and nothing else as root
echo "slicks ALL=(root) NOPASSWD: /usr/bin/systemctl restart slicks-erp, /usr/bin/systemctl stop slicks-erp, /usr/bin/systemctl start slicks-erp, /usr/bin/systemctl restart slicks-streamlit, /usr/bin/systemctl stop slicks-streamlit, /usr/bin/systemctl start slicks-streamlit" \
  > /etc/sudoers.d/slicks-erp && chmod 440 /etc/sudoers.d/slicks-erp && visudo -cf /etc/sudoers.d/slicks-erp

echo "== Python environment"
[ -x /srv/slicks/venv/bin/python ] || sudo -u slicks python3 -m venv /srv/slicks/venv

echo "== settings file (secrets stay on the server)"
install -d -m 750 -o root -g slicks /etc/slicks-erp
if [ ! -f /etc/slicks-erp/env ]; then
  KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(60))')"
  sed -e "s|__SITE__|$SITE|" -e "s|__DBFILE__|$DBFILE|" -e "s|__LIVE__|$LIVE|" -e "s|__SECRET__|$KEY|" "$HERE/env.template" > /etc/slicks-erp/env
  chown root:slicks /etc/slicks-erp/env && chmod 640 /etc/slicks-erp/env
fi

echo "== services"
install -m 644 "$HERE/slicks-erp.service" /etc/systemd/system/slicks-erp.service
install -m 644 "$HERE/slicks-streamlit.service" /etc/systemd/system/slicks-streamlit.service
install -m 644 "$HERE/slicks-erp-nightly.service" /etc/systemd/system/slicks-erp-nightly.service
install -m 644 "$HERE/slicks-erp-nightly.timer" /etc/systemd/system/slicks-erp-nightly.timer
sed "s|__SITE__|$SITE|" "$HERE/Caddyfile.template" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable slicks-erp slicks-streamlit slicks-erp-nightly.timer
systemctl start slicks-erp-nightly.timer
systemctl reload caddy || systemctl restart caddy

echo "== SSH: keys only, no root login"
cat > /etc/ssh/sshd_config.d/10-slicks.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
SSHD
sshd -t && systemctl reload ssh

echo "== firewall"
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable
systemctl enable --now fail2ban

echo
echo "Done. From the laptop: deploy/upload_data.sh slicks@$SITE <database file>, then deploy/deploy_to_server.sh slicks@$SITE"
echo "From now on sign in as slicks (root sign-in is off)."
