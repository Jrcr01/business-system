# Slicks ERP on the server (step F3)

Owner's choices (2026-09-28): **the ERP test site first** (a copy of the sandbox data at `test.<domain>`; the live AP stays on the
laptop until the cutover), **DigitalOcean** (Singapore), **Google sign-in** (added in F3.3), a new domain the owner registers.

## What runs where

| On the server | Why |
|---|---|
| Ubuntu 24.04, user `slicks` (SSH key only; root and passwords switched off) | Only the laptop's key can sign in to the server |
| Firewall: SSH, 80, 443 only; fail2ban; automatic security updates | Nothing else is reachable from the internet |
| Caddy (`/etc/caddy/Caddyfile`) | HTTPS with an automatic, self-renewing certificate |
| `slicks-erp` service (gunicorn, 2 workers, `127.0.0.1:8000` only) | The ERP; restarts by itself after a crash or reboot |
| `slicks-erp-nightly.timer` (18:00 Manila) → `deploy/nightly.py` | Audit log check + seal, then a backup to `/srv/slicks/backups` (newest 30) |
| `/etc/slicks-erp/env` | The settings and the secret key — only on the server, never in git |
| `/srv/slicks/data/` | The test site's database + its audit seal key / state |

## Steps

**F3.1 (done on the laptop):** this folder, production settings (whitenoise, trusted HTTPS origins, logging, `ERP_SITE_LABEL`
banner), the home-screen app (manifest, icons, a service worker that never stores business pages on the phone).

**Names (owner 2026-09-28):** the ERP at **erp.slickstiresupply.com**, the test site at **test.slickstiresupply.com**; the bare
domain stays free for a public website. The laptop key `~/.ssh/slicks_erp` has a passphrase only the owner types (`ssh-add` once;
Windows' ssh-agent keeps it unlocked); the deploy scripts use Windows' own ssh/scp for that reason.

**F3.2 — the owner, guided:**
1. Register the domain (e.g. at Cloudflare) and keep its DNS there.
2. Create the DigitalOcean account, then a Droplet: Ubuntu 24.04, Singapore, the smallest "Basic" size that has 1 GB memory,
   weekly backups on, and **SSH key** = the laptop's public key (Claude makes the key pair on the laptop; the private half never
   leaves the laptop).
3. In the DNS: an `A` record `test` → the Droplet's IP address.

**Then, from the laptop (Git Bash, `C:\Slicks\Slicks_ERP`):**
```
scp -r deploy root@<droplet-ip>:/root/deploy
ssh root@<droplet-ip> "bash /root/deploy/bootstrap_server.sh test.<domain>"
deploy/upload_test_data.sh slicks@test.<domain>
deploy/deploy_to_server.sh slicks@test.<domain>
```
After that, every update is one line: commit, then `deploy/deploy_to_server.sh slicks@test.<domain>` (it refuses uncommitted
changes, installs pinned packages, migrates, checks the security settings, switches to the new release and **switches back by
itself if the ERP does not answer**).

**F3.3 — Google sign-in** (the owner creates the Google sign-in client; only emails already on the Users page can sign in).

**F3.4 — try it:** open `https://test.<domain>` on the Samsungs → Chrome menu → **Install app**.

## Rules
* The test site never holds the live database (settings refuse `ap_disbursement.db` without `ERP_LIVE=1`; the upload script only
  copies the sandbox). The live move is the cutover (F5), with its own checklist.
* Secrets only in `/etc/slicks-erp/env` on the server. Nothing in this folder is secret.
