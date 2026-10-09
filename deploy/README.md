# Slick's system on the server: go-live, side by side (2026-10-03)

**Owner's choices:**

- **Go live as soon as possible.** On the server, the **Django system** is the main site. The project's **Streamlit app** runs beside it on the **same database**, for the screens not moved to Django yet (at `/more/`). Each later stage moves a screen across; at the end Streamlit is switched off.
- **Domain `slickstiresupply.com`** is at Cloudflare. The system is at **erp.slickstiresupply.com**; the bare domain stays free for a public website.
- **Server:** DigitalOcean, Singapore, Ubuntu 24.04.

## What runs where

| On the server | Why |
|---|---|
| Ubuntu 24.04, user `slicks` (SSH key only; root and passwords switched off) | Only the laptop's key can sign in |
| Firewall: SSH, 80, 443 only; fail2ban; automatic security updates | Nothing else is reachable from the internet |
| Caddy (`/etc/caddy/Caddyfile`) | HTTPS with a self-renewing certificate. `/more/...` goes to Streamlit, everything else to Django |
| `slicks-erp` service: Django, gunicorn, 3 workers, `127.0.0.1:8000` | The main system; restarts by itself |
| `slicks-streamlit` service: Streamlit, `127.0.0.1:8501`, at `/more/` | The screens not moved yet; same database, its own sign-in with the same accounts |
| `slicks-erp-nightly.timer` (18:00 Manila) runs `deploy/nightly.py` | Audit log check and seal, then a database backup (newest 30 kept) |
| `/etc/slicks-erp/env` | The settings and the secret key; only on the server, never in git |
| `/srv/slicks/data/` | The one database (and its audit seal key and state) |
| `/srv/slicks/backups/` | Nightly backups, plus a copy before every release and before any database is replaced |
| Tesseract | Reads invoice photos (Add invoices from photos) |

## Steps

### 1. The owner (once)

1. **Domain:** done (`slickstiresupply.com` at Cloudflare).
2. **DigitalOcean account:** with two-step sign-in and a payment method.
3. **SSH key on the laptop.** In Git Bash, run `ssh-keygen -t ed25519 -f ~/.ssh/slicks_erp -C "slicks-erp"` with a passphrase only you know. Then run `ssh-add ~/.ssh/slicks_erp` once, so Windows remembers it.
4. **Create the Droplet:**
   - Ubuntu 24.04, Singapore, Basic, **2 GB** memory (owner resized from 1 GB on 2026-10-08; the setup adds a 2 GB swap file and runs 3 Django workers). Turn weekly backups on.
   - **SSH key** = paste the contents of `~/.ssh/slicks_erp.pub` (the public half only).
5. **Point the name at the server.** In Cloudflare DNS, add an `A` record: name `erp`, value = the Droplet's IP address, proxy **DNS only** (grey cloud) for now.

### 2. From the laptop (Git Bash, `C:\Slicks\Slicks_ERP`)

```
scp -r deploy root@<droplet-ip>:/root/deploy
ssh root@<droplet-ip> "bash /root/deploy/bootstrap_server.sh erp.slickstiresupply.com live"
```

From here on, sign in as `slicks` (root sign-in is switched off).

### 3. Rehearsal (before the real move)

1. Upload a **copy** of the live data:

   ```
   deploy/upload_data.sh slicks@erp.slickstiresupply.com <copy of the live database>
   ```

2. Deploy:

   ```
   deploy/deploy_to_server.sh slicks@erp.slickstiresupply.com
   ```

   It installs the pinned packages, copies the database, adds what schema.sql has that the database lacks (it never changes or removes anything), checks the security settings, switches to the new release and restarts both programs. If the system does not answer, it switches back by itself.

3. Open https://erp.slickstiresupply.com, sign in, and check the totals against the laptop's AP.

### 4. The move (cutover), only with the owner's go-ahead

1. Back up the live database on the laptop.
2. Stop the laptop's AP.
3. Upload the live file with `deploy/upload_data.sh`. It asks before replacing the rehearsal copy and keeps that copy.
4. Deploy, then check the totals again.

From then on, everyone uses https://erp.slickstiresupply.com, and the laptop's AP stays stopped.

### Every update afterwards

Commit, then run:

```
deploy/deploy_to_server.sh slicks@erp.slickstiresupply.com
```

Only committed code is sent; the server never needs GitHub access.

### The release routine, now that the family works in the system (owner, 2026-10-09)

1. **Build and test on the sandbox.** The sandbox is at port 8505. The owner approves it there.
2. **Run the full test suite.** It must pass.
3. **Rehearse big changes.** A change is "big" when it brings a new migration or touches the money, clearing or permission rules.
   - Run `deploy/rehearse.sh slicks@165.245.188.246`. It copies last night's live backup to `data/rehearsal/` and applies the update to it.
   - Click through the changed pages on the 8508 copy, as the owner and as a co-owner.
   - Delete `data/rehearsal/` afterwards.
4. **Write the What's new note.** Add 1–3 plain lines at the top of `engines/whats_new.py`, in the same commit. Each person sees them once on Home and presses **Got it**.
5. **Optional banner.** For a change that alters how people work, post a notice in Settings with "On every page until". For example: "System update tonight at 12 AM, about 1 minute".
6. **Deploy.** Run `deploy/deploy_to_server.sh slicks@165.245.188.246`.
   - **Big changes go live only between 12 AM and 6 AM Manila.** At any other hour the script stops.
   - **Small changes can go any time,** but every deploy first lists who was active in the last 10 minutes. If anyone was, the script stops.
   - `--anyway` skips both checks. Use it only for an urgent fix the owner approved.
   - After the release, the commit is tagged `live-<release>`. The script then waits about 20 seconds and prints any errors from the server log since the restart.
7. **If it goes wrong:** run `deploy/rollback.sh slicks@165.245.188.246`.
   - It puts the previous release back in seconds. Only the code changes; nothing typed since the update is lost, because migrations only add.
   - Restoring the database from a `before_release_*` backup is a last resort, done only with the owner's OK, because it undoes the entries made since.

## Rules

- **One database for both programs.** The Django settings refuse a file named `ap_disbursement.db` without `WEB_LIVE=1`, and only the live setup sets it.
- **Secrets** live only in `/etc/slicks-erp/env` on the server. Nothing in this folder is secret.
- **Streamlit** is never reachable directly from the internet, only through Caddy at `/more/`. Its sign-in is always on (`AP_SKIP_LOGIN` is never set on the server).
