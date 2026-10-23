# Deploying to a VPS

A single-server setup on Debian 13 (trixie): the tome bank under a dedicated
user, the MCP server on loopback, a capture timer, and Caddy in front for TLS
and basic auth. Nothing else is exposed.

```
agent:  claude mcp add ... https://tome.example.com/mcp  (basic auth)
                    │
                    ▼
              Caddy :443  ── /mcp* ──▶ 127.0.0.1:8765  tome serve
              (TLS, basic auth)                         │
                                                        ▼
                                          /var/lib/tome/bank  (git)
                                                        ▲
              tome-ingest.timer ── every 30 min ── tome ingest inbox
                                                   tome ingest nyttig
```

| File                                  | Installed to                        |
|---------------------------------------|-------------------------------------|
| `systemd/tome.service`                | `/etc/systemd/system/`              |
| `systemd/tome-ingest.service`         | `/etc/systemd/system/`              |
| `systemd/tome-ingest.timer`           | `/etc/systemd/system/`              |
| `Caddyfile`                           | `/etc/caddy/Caddyfile`              |
| the repo (code)                       | `/opt/tome/`                        |
| `/etc/tome/tome.env` (you write it)   | `/etc/tome/tome.env`                |

**Sizing:** 1 vCPU and 1 GB RAM are plenty for Phase 1. The index is SQLite
and the bank is Markdown plus git history; both grow slowly with use.

Commands below run on the server as a sudo user, unless marked otherwise.
Replace `tome.example.com` everywhere.

## 1. Packages and user

```bash
sudo apt install git python3-venv
python3 --version                  # 3.12 or newer (Debian 13 ships 3.13)
caddy version                      # 2.8 or newer, for basic_auth
```

Install Caddy from [Caddy's apt repository](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
Debian's own package is older than 2.8.

Create the service user. It has no login shell:

```bash
sudo useradd --system --home-dir /var/lib/tome --shell /usr/sbin/nologin tome
sudo install -d -o tome -g tome -m 0750 /var/lib/tome
```

## 2. Code

Put the code in `/opt/tome` and build a virtualenv there. It is owned by root
and readable by everyone, so the `tome` user can run it but not change it:

```bash
sudo git clone https://github.com/<you>/tome.git /opt/tome
sudo python3 -m venv /opt/tome/.venv
sudo /opt/tome/.venv/bin/pip install /opt/tome
/opt/tome/.venv/bin/tome --help    # checks the install
```

A non-editable install (no `-e`) is deliberate. The service runs the installed
copy, and an update is a pull plus a reinstall (see [Updating](#updating)).

## 3. The bank

The bank is a git repository of its own, separate from the code. Create it as
the `tome` user so every file in it is theirs:

```bash
sudo -u tome /opt/tome/.venv/bin/tome init /var/lib/tome/bank
```

This writes `TOME.md`, `raw/`, `wiki/` (with `index.md` and `log.md`), `inbox/`
and an initial commit. `tome init` is idempotent: running it again leaves
existing files alone.

## 4. Environment file

The services read `/etc/tome/tome.env`. The file holds the nyttig password, so
it is root-owned and readable by the `tome` group only:

```bash
sudo install -d -m 0755 /etc/tome
sudo install -m 0640 -o root -g tome /dev/null /etc/tome/tome.env
sudoedit /etc/tome/tome.env
```

Put this in it:

```bash
TOME_HOME=/var/lib/tome/bank
TOME_ALLOWED_HOSTS=tome.example.com
TOME_ALLOWED_ORIGINS=https://tome.example.com

# nyttig, for the ingest timer and the ingest_nyttig tools. Use the origin
# only: tome adds /api/... itself. The user and password are the basic auth
# that nyttig's Caddy puts in front of /api.
TOME_NYTTIG_URL=https://nyttig.example.com
TOME_NYTTIG_USER=you
TOME_NYTTIG_PASSWORD=change-me

# Optional: the author of bank commits. Defaults to "tome <tome@localhost>".
#TOME_GIT_NAME=tome
#TOME_GIT_EMAIL=tome@example.com
```

| Variable               | Set to                                | Why                                                    |
|------------------------|---------------------------------------|--------------------------------------------------------|
| `TOME_HOME`            | `/var/lib/tome/bank`                  | The bank the services open                             |
| `TOME_ALLOWED_HOSTS`   | `tome.example.com`                    | Caddy forwards the `Host` header unchanged; without this the server answers 421 |
| `TOME_ALLOWED_ORIGINS` | `https://tome.example.com`            | Same check for `Origin`                                |
| `TOME_NYTTIG_URL`      | `https://nyttig.example.com`          | Enables the nyttig tools and `tome ingest nyttig`      |
| `TOME_NYTTIG_USER`     | your nyttig basic auth user           | Sent with each nyttig request                          |
| `TOME_NYTTIG_PASSWORD` | your nyttig basic auth password       | Sent with each nyttig request                          |

Leave `TOME_NYTTIG_URL` out if you do not use nyttig. The timer's nyttig line
then fails with "TOME_NYTTIG_URL is not set", which the `-` prefix tolerates.
Remove that line from `tome-ingest.service` to keep the journal clean.

## 5. Systemd units

```bash
sudo install -m 0644 /opt/tome/deploy/systemd/tome.service \
  /opt/tome/deploy/systemd/tome-ingest.service \
  /opt/tome/deploy/systemd/tome-ingest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tome.service tome-ingest.timer
```

`tome.service` runs `tome serve` on `127.0.0.1:8765` as `tome`. It restarts on
failure. `tome-ingest.timer` starts `tome-ingest.service` every 30 minutes
(with up to 2 minutes of random delay) and once at boot if a run was missed.

The ingest service runs two commands in order:

- `tome ingest inbox` captures whatever is in `inbox/`.
- `tome ingest nyttig --since 2d --limit 500` captures new nyttig archives.
  Its line has a `-` prefix, so a nyttig failure does not mark the run failed.

`--since 2d` limits the query to items from the last two days. `--limit 500`
is the most items read per run. Items already captured are skipped without a
download, so a run that finds nothing new usually makes one API call.

Check the timer and the first run:

```bash
systemctl list-timers tome-ingest.timer
sudo systemctl start tome-ingest.service        # run once now
journalctl -u tome-ingest -n 50 -o cat
```

## 6. Caddy

```bash
sudo install -m 0644 /opt/tome/deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy hash-password                        # type a long password; copy the hash
sudoedit /etc/caddy/Caddyfile                   # set the host, user and hash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

The site serves only `/mcp` (and `/mcp/...`) behind basic auth. Any other path
returns 404. Caddy obtains the TLS certificate itself once the DNS record
points at the server and ports 80 and 443 are open.

Open the firewall for SSH and the web:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
```

Port 8765 is not opened. `tome serve` listens on loopback only.

## 7. Verify

From the server, the MCP endpoint answers. A plain GET is not a valid MCP
request, so the server replies with a 400 and a JSON error. A connection
refused would mean tome is not running:

```bash
curl -i http://127.0.0.1:8765/mcp              # 400 with a JSON-RPC error
curl -s -o /dev/null -w '%{http_code}\n' https://tome.example.com/       # 404
curl -s -o /dev/null -w '%{http_code}\n' https://tome.example.com/mcp    # 401: no credentials
curl -i -u you:PASSWORD https://tome.example.com/mcp                    # 400 JSON error: auth passed
```

Check the bank as the service user (this reads the env file, which is
group-readable by `tome`):

```bash
sudo -u tome sh -c 'set -a; . /etc/tome/tome.env; exec /opt/tome/.venv/bin/tome status'
systemctl status tome --no-pager
journalctl -u tome -n 50 -o cat
```

`tome status` prints document and chunk counts, the 10 most recent commits, and
the number of raw sources no wiki page cites yet.

Connect an agent from your workstation. Claude Code, with the basic auth
header:

```bash
claude mcp add --transport http tome https://tome.example.com/mcp \
  --header "Authorization: Basic $(printf 'you:PASSWORD' | base64)"
```

## 8. Dropping files into the inbox

`inbox/` belongs to `tome`, and `tome` does not log in. Two ways to get files in:

```bash
# A. From your workstation, through a sudo-capable login:
scp notes.pdf vps:/tmp/
ssh vps 'sudo install -m 0644 -o tome -g tome /tmp/notes.pdf /var/lib/tome/bank/inbox/ && rm /tmp/notes.pdf'

# B. A group for drop-off (once, on the server), so your user can write there:
sudo groupadd tome-drop
sudo usermod -aG tome-drop <you>                # log in again afterwards
sudo chgrp tome-drop /var/lib/tome/bank/inbox
sudo chmod 2775 /var/lib/tome/bank/inbox       # setgid keeps the group
```

With option B, `scp notes.pdf vps:/var/lib/tome/bank/inbox/` works directly.
The next timer run (within 30 minutes) captures the file, commits it and
removes it from `inbox/`. Files modified in the last 10 seconds wait for a
later run, so a copy in progress is not picked up half-written.

Files that tome cannot capture (unsupported types, empty files, unreadable
files) stay in `inbox/`. The run prints them as `left in inbox`.

## 9. Backups

The bank's history is in git, but the only copy is on this server. Push the
bank to a private remote on each change, from a cron job or a second timer.

The `tome` user needs an SSH key for the remote. The unit sets `HOME` to
`/var/lib/tome`, so the key goes there:

```bash
sudo -u tome ssh-keygen -t ed25519 -N '' -f /var/lib/tome/.ssh/id_ed25519
sudo -u tome cat /var/lib/tome/.ssh/id_ed25519.pub   # add it as a deploy key, write access
sudo -u tome git -C /var/lib/tome/bank remote add origin git@github.com:<you>/tome-bank.git
sudo -u tome git -C /var/lib/tome/bank push -u origin main
```

Then, from a timer or cron, run `sudo -u tome git -C /var/lib/tome/bank push -q`.

The index (`.tome/index.db`) is not backed up. It is derived, and
`tome reindex --full` rebuilds it from the bank.

## Updating

Pull the code, reinstall, restart. The bank is untouched:

```bash
sudo git -C /opt/tome pull
sudo /opt/tome/.venv/bin/pip install /opt/tome
sudo install -m 0644 /opt/tome/deploy/systemd/tome.service \
  /opt/tome/deploy/systemd/tome-ingest.service \
  /opt/tome/deploy/systemd/tome-ingest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart tome tome-ingest.timer
sudo -u tome sh -c 'set -a; . /etc/tome/tome.env; exec /opt/tome/.venv/bin/tome reindex'
```

Compare the `deploy/` files in the new checkout with the installed copies
before you copy them over, in case a release changed the units or the
Caddyfile. Reindexing after an update is optional: tome rebuilds the index
when its schema changes, and `reindex --full` is the manual fallback.
