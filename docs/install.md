# Install guide: self-hosted TalentSift

**Bottom line:** one small Linux VM, Python 3.11, one service, one nightly backup. About 20 minutes. One install
serves one employer organization. The MVP has no login, so step 6 (a password in front of the app) is required
for anything reachable from the internet.

These steps follow the deployment memo ([deployment_memo.md](deployment_memo.md)). They were written for
Ubuntu 22.04 or 24.04 and have **not yet been run on the demo VM**: T33 is the first real run. Record anything
that differs and fix this guide in the same pull request.

## 1. Server and user

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv git
sudo useradd --system --create-home --home-dir /opt/talentsift --shell /usr/sbin/nologin talentsift
sudo mkdir -p /var/lib/talentsift && sudo chown talentsift:talentsift /var/lib/talentsift
```

On a 1 GB VM, add 1 GB of swap before installing.

## 2. Code

```bash
sudo -u talentsift git clone https://github.com/grant-hauskins/TalentSift.git /opt/talentsift
cd /opt/talentsift
sudo -u talentsift git checkout <release tag>
sudo -u talentsift python3.11 -m venv .venv
sudo -u talentsift .venv/bin/pip install -r requirements.txt
```

## 3. Configuration

```bash
sudo -u talentsift cp .env.example .env
sudo chmod 600 .env
```

Set these in `/opt/talentsift/.env`:

| Setting | Value | Why |
|---------|-------|-----|
| `TALENTSIFT_DATA_DIR` | `/var/lib/talentsift` | Database, uploads, and backups live outside the code folder, so updates never touch data |
| `OPENROUTER_API_KEY` | your key | Set a spending limit on the key at openrouter.ai. Never commit it |
| `LLM_MODEL` | `anthropic/claude-sonnet-4.5` | Or choose on the Settings page |
| `COST_CAP_USD` | `2.00` | Per screening batch |
| `ALLOW_FOLDER_IMPORT` | `false` | Visitors must not browse the server's disk. Use upload |
| `AUTO_SEED_DEMO` | `false` | The disk is persistent; seed once by hand (step 4) |

Leave `LLM_PROVIDER` empty: it uses OpenRouter when a key is set and the offline client otherwise.

## 4. First run and demo data

```bash
sudo -u talentsift .venv/bin/python scripts/seed_demo.py --screen     # optional: 2 roles, 26 fictional resumes
sudo cp deploy/talentsift.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now talentsift
systemctl status talentsift
```

The app now listens on `127.0.0.1:8501`.

## 5. Nightly backup

```bash
sudo -u talentsift .venv/bin/python scripts/backup.py     # test it once by hand
sudo crontab -u talentsift -e
```

Add this line (02:00 every night):

```
0 2 * * * cd /opt/talentsift && .venv/bin/python scripts/backup.py >> /var/lib/talentsift/backup.log 2>&1
```

Each archive holds a consistent database snapshot and the uploaded resumes. The newest 14 are kept
(`BACKUP_KEEP`). Backups sit on the same disk as the data: copy them off the VM weekly.

**Restore**

```bash
sudo systemctl stop talentsift
sudo -u talentsift .venv/bin/python scripts/backup.py --list
sudo -u talentsift .venv/bin/python scripts/backup.py --restore /var/lib/talentsift/backups/talentsift-YYYYMMDD-HHMMSS.tar.gz
sudo systemctl start talentsift
```

The data that was replaced is moved to `backups/replaced-<time>/`, not deleted.

## 6. A password in front of the app (required)

TalentSift has no login. Put a reverse proxy with a password in front of it and open only that port. With Caddy:

```
your-host.example {
    basic_auth {
        team <hash from `caddy hash-password`>
    }
    reverse_proxy 127.0.0.1:8501
}
```

(`basic_auth` is the spelling in Caddy 2.8 and later; older versions use `basicauth`.)

No domain? Keep the firewall closed and reach the app through an SSH tunnel:

```bash
ssh -L 8501:127.0.0.1:8501 you@the-vm     # then open http://localhost:8501
```

## 7. Update

```bash
sudo -u talentsift .venv/bin/python scripts/backup.py
sudo systemctl stop talentsift
sudo -u talentsift git fetch --tags && sudo -u talentsift git checkout <new tag>
sudo -u talentsift .venv/bin/pip install -r requirements.txt
sudo systemctl start talentsift
```

On start, new database columns are added in place. Nothing is dropped.

## 8. Closeout

Planning Report rule: only sample or consented resumes are used, and they are deleted at closeout.

```bash
sudo systemctl stop talentsift
sudo rm -rf /var/lib/talentsift/resumes/uploads /var/lib/talentsift/talentsift.db* /var/lib/talentsift/backups
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Sidebar says "No API key or model yet" | `.env` values, then `sudo systemctl restart talentsift` |
| A run stops with "cost cap reached" | Raise `COST_CAP_USD`, or re-run: cached scores are free |
| Resumes land in Needs review with "name could not be found and masked" | Expected for unusual layouts. Read them by hand |
| AI outage during a demo | Pick "Offline fake client" in the sidebar |
