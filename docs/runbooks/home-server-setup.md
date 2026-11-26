# Runbook: the Friday home server (one server, many nodes)

ADR-017 (home hub topology, Accepted 2026-11-26), Phase 13
(`docs/prompts/phase-13-home-hub/brief.md`). How to turn a Linux mini PC on
the home LAN into the one always-on Friday server that every PC pairs with
as a node: encrypted disk, a fixed LAN address and name, the Docker
Compose stack, TLS for the node gateway from a private CA, a LAN-only
firewall, nightly encrypted off-site backups with a restore drill,
upgrades, and the later move to a VPS.

**What exists today and what Phase 13 adds.** Steps 1–7 use only what is
on the integration branch now (`deploy/docker/compose.yml` +
`compose.daily.yml`, `make up-daily`, `manage.py preflight`,
`verify_node_audit_chain`). Phase 13 branch 6 (`feat/p13-home-server-ops`)
moves the backup and drill scripts below into `deploy/home/`, adds
`deploy/docker/compose.home.yml` and the `home_*` preflight rows that read
the status files these scripts write. Until it merges, use the scripts as
written here; their status-file format is the one branch 6 pins.

Placeholders used throughout: home subnet `192.168.1.0/24`, server address
`192.168.1.20`, mDNS name `friday-home.local`, user `friday`. Replace them
with your own values.

---

## 0. Hardware and prerequisites

- An x86-64 mini PC with at least 4 cores, 16 GB RAM and a 500 GB SSD
  (Postgres + pgvector, Redis, the worker, the local speech server if you
  run it here). A wired Ethernet connection is strongly preferred.
- Ubuntu Server LTS (24.04 or newer LTS) installation USB.
- A second machine (one of your PCs) for the private CA's root key and the
  backup decryption key — neither lives on the server.
- Optional: an off-site backup target (a Cloudflare R2 bucket, any
  S3-compatible bucket, or an SFTP host). A second local disk is acceptable
  until you choose one; record that as "off-site pending" in step 8.

## 1. Install Ubuntu with full-disk encryption (LUKS)

1. Boot the installer and choose **"Use an entire disk"** with
   **"Set up this disk as an LVM group"** and **"Encrypt the LVM group with
   LUKS"**. Choose a long passphrase and store it in your password manager.
2. Create the user `friday` (sudo). Enable the OpenSSH server.
3. After the first boot, check the encryption:

   ```bash
   lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINT   # a crypt device under the root LV
   sudo cryptsetup status dm_crypt-0 2>/dev/null || sudo dmsetup ls --target crypt
   ```

4. Unattended reboot (optional): the passphrase is asked at every boot. If
   the box must come back after a power cut without you, bind the LUKS key
   to the TPM (`systemd-cryptenroll --tpm2-device=auto /dev/<luks-partition>`)
   and keep the passphrase as the fallback slot. This trades some
   protection (a stolen machine that boots unlocks itself) for
   availability; decide consciously and write it down.
5. Updates: `sudo apt update && sudo apt full-upgrade -y`, then enable
   unattended security updates: `sudo dpkg-reconfigure -plow unattended-upgrades`.

Record the date in `deploy/docker/.env` once the stack exists (step 4):
`HOME_FDE_CONFIRMED_ON=YYYY-MM-DD` (read by the `home_disk_encryption`
preflight row after branch 6; the container cannot see the host's disk
layout, so this is your attestation).

## 2. A fixed LAN address and an mDNS name

Prefer a **DHCP reservation** on the router (bind the server's MAC address
to `192.168.1.20`): nothing on the server changes and the router stays the
single source of addresses. If the router cannot reserve, set a static
address with netplan:

```yaml
# /etc/netplan/60-friday.yaml  (chmod 600)
network:
  version: 2
  ethernets:
    enp1s0:            # `ip link` shows the real name
      dhcp4: false
      addresses: [192.168.1.20/24]
      routes: [{to: default, via: 192.168.1.1}]
      nameservers: {addresses: [192.168.1.1]}
```

```bash
sudo netplan apply
```

mDNS name, so PCs reach `friday-home.local` without touching the router's
DNS:

```bash
sudo hostnamectl set-hostname friday-home
sudo apt install -y avahi-daemon
avahi-resolve -n friday-home.local          # from another PC: ping friday-home.local
```

Windows PCs resolve `.local` names via mDNS on current Windows releases;
if one does not, add `192.168.1.20 friday-home.local` to its hosts file.

## 3. Docker Engine and Compose

Install Docker Engine and the Compose plugin from Docker's apt repository
(the steps at docs.docker.com → "Install Docker Engine on Ubuntu"), then:

```bash
sudo usermod -aG docker friday     # log out and back in
docker compose version
```

**Docker and the firewall.** Ports Docker publishes bypass `ufw`. The
Friday stack publishes every port on `127.0.0.1` only
(`deploy/docker/compose.yml`), and it must stay that way: the only
LAN-facing listener is the reverse proxy of step 5, which runs on the
host, not in Docker. Never change a `127.0.0.1:` port mapping to `0.0.0.0`.

## 4. The Friday stack

```bash
sudo mkdir -p /srv/friday && sudo chown friday: /srv/friday
cd /srv/friday
git clone <your Friday repository URL> friday && cd friday
git checkout main                       # or the tag you run
make secrets                            # writes deploy/docker/.env (0600)
```

Edit `deploy/docker/.env` (never commit it):

- `DJANGO_ALLOWED_HOSTS=friday-home.local,192.168.1.20,localhost,web`
- `TRUSTED_CLIENT_IP_HEADER=X-Forwarded-For` (Caddy sets it; the BFF
  forwards the client IP so PIN pairing stays LAN-gated,
  `DEVICE_PAIRING_NETWORKS`).
- `DEVICE_PAIRING_ALLOW_UNKNOWN_IP=false` (the production default).
- `NODE_SIGNING_KEY` (generated by `make secrets`; see
  `docs/runbooks/friday-node.md` "Signing key"). **Back it up with the
  `.env` in step 8** — every paired node pins it.
- The provider keys, HA and connector settings you use (see
  `.env.example`; secrets only in this file).

Start the daily-driver stack and run preflight:

```bash
make up-daily
docker compose -f deploy/docker/compose.yml -f deploy/docker/compose.daily.yml ps
make preflight                          # every row pass or a known warn
```

After Phase 13 branch 6 merges, start with the home override instead:
`docker compose -f deploy/docker/compose.yml -f deploy/docker/compose.daily.yml
-f deploy/docker/compose.home.yml up -d --build` (adds the backup status
volume mounts and the `home_*` settings; still nothing on `0.0.0.0`).

## 5. TLS for the frontend and the node gateway (private CA)

The node WSS connection is TLS with a pinned certificate or CA even on the
LAN (ADR-013 decision 2, ADR-017 decision 4). Create a small private CA on
**another PC** (the root key never lives on the server):

```bash
# On your PC, in an encrypted folder:
openssl ecparam -name prime256v1 -genkey -noout -out friday-home-ca.key
openssl req -x509 -new -key friday-home-ca.key -sha256 -days 3650 \
  -subj "/CN=Friday Home CA" -out friday-home-ca.crt \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

# The server key: generated once and REUSED on every renewal, so the
# served SPKI (the node pin) never changes.
openssl ecparam -name prime256v1 -genkey -noout -out friday-home.key
openssl req -new -key friday-home.key -subj "/CN=friday-home.local" -out friday-home.csr
openssl x509 -req -in friday-home.csr -CA friday-home-ca.crt -CAkey friday-home-ca.key \
  -CAcreateserial -days 825 -sha256 -out friday-home.crt \
  -extfile <(printf "subjectAltName=DNS:friday-home.local,IP:192.168.1.20\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature")
```

Copy `friday-home.crt` and `friday-home.key` to the server
(`/etc/caddy/tls/`, owner `caddy`, key mode 0600). Install
`friday-home-ca.crt` as a trusted root on every PC whose browser opens
Friday (macOS Keychain "System" → Always Trust; Ubuntu
`/usr/local/share/ca-certificates/` + `update-ca-certificates`; Windows
"Trusted Root Certification Authorities").

Install Caddy on the host (the `caddy` package from Ubuntu or Caddy's apt
repository) and bind it to the LAN address only:

```caddyfile
# /etc/caddy/Caddyfile
{
    auto_https off
    admin off
}

https://friday-home.local:443, https://192.168.1.20:443 {
    bind 192.168.1.20
    tls /etc/caddy/tls/friday-home.crt /etc/caddy/tls/friday-home.key

    @node path /ws/node/v1
    handle @node {
        reverse_proxy 127.0.0.1:8002
    }
    handle {
        reverse_proxy 127.0.0.1:3001     # the frontend (BFF)
    }
}
```

```bash
sudo systemctl reload caddy
curl --cacert friday-home-ca.crt https://friday-home.local/   # from a PC
```

Pin the served key for preflight (`docs/runbooks/friday-node.md`, "Pinning
the served certificate"):

```bash
openssl s_client -connect 192.168.1.20:443 -servername friday-home.local </dev/null 2>/dev/null \
  | openssl x509 -pubkey -noout | openssl pkey -pubin -outform der \
  | openssl dgst -sha256 -binary | base64
```

Put it in `deploy/docker/.env` as `NODE_TLS_PIN_SHA256=` together with
`PREFLIGHT_NODE_TLS_URL=https://friday-home.local/ws/node/v1`, restart web,
and `node_tls_pin` must pass. **Renewal:** re-sign a new certificate from
the same `friday-home.key` before day 825; the SPKI and every node pin stay
valid. A new key means a re-pin and a re-pair of every node.

## 6. Firewall: LAN only

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 443 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 5353 proto udp   # mDNS
sudo ufw enable
sudo ufw status verbose
```

- No port forwarding on the router, no UPnP for this machine, no tunnel
  service. Friday is home-only (ADR-017 decision 3).
- Check from a PC: `nc -vz 192.168.1.20 8001` and `… 8002` must **fail**
  (published on 127.0.0.1 only); `… 443` succeeds.
- Optional Tailscale: install it, `sudo tailscale up`, then
  `sudo ufw allow in on tailscale0 to any port 443 proto tcp`, add the
  Tailscale address to the certificate's SAN (re-sign from the same key)
  and to `DJANGO_ALLOWED_HOSTS`. Do not enable Tailscale Funnel.
- Home Assistant stays where it is on the LAN; `HA_BASE_URL` points at its
  LAN address (`docs/mcp/home-assistant.md`).

## 7. Pair the PCs as nodes

Every PC becomes a node of this server; none runs its own Friday.

1. Open `https://friday-home.local/devices` on the PC, sign in as the
   owner.
2. Mac: "Mac eşleştir" issues a LAN-only PIN; follow
   `docs/runbooks/friday-node.md` (the Swift `friday-node`, Mac session).
   Linux PC: after Phase 13 branch 4, follow
   `node/friday-node-linux/README.md` (`friday-node-linux pair`).
3. After Phase 13 branch 1: give each node a name, a room and a role on
   `/devices` ("Çalışma odası MacBook", room `çalışma odası`, role
   `workstation`). Names are what you say ("çalışma odasındaki Mac'te").
4. `make preflight`: `node_link` lists every paired node.

## 8. Nightly encrypted backups (pg_dump + age + restic)

**Keys, made once on your PC (not on the server):**

```bash
age-keygen -o friday-backup.agekey          # private key: password manager + an offline copy
grep 'public key' friday-backup.agekey      # age1… — this goes to the server
```

On the server:

```bash
sudo apt install -y age restic
sudo install -d -m 0700 -o friday /var/backups/friday /etc/friday-backup
echo 'age1…' | sudo tee /etc/friday-backup/age.pub
# restic repository password (random, also stored in your password manager):
openssl rand -base64 32 | sudo tee /etc/friday-backup/restic.pass >/dev/null
sudo chmod 600 /etc/friday-backup/restic.pass; sudo chown -R friday: /etc/friday-backup
```

Off-site target, e.g. Cloudflare R2 (S3-compatible; create a bucket and an
API token scoped to that bucket only):

```bash
# /etc/friday-backup/restic.env  (0600, owner friday)
RESTIC_REPOSITORY=s3:https://<account-id>.r2.cloudflarestorage.com/friday-backup
RESTIC_PASSWORD_FILE=/etc/friday-backup/restic.pass
AWS_ACCESS_KEY_ID=<r2 key id>
AWS_SECRET_ACCESS_KEY=<r2 secret>
```

```bash
set -a; . /etc/friday-backup/restic.env; set +a; restic init
```

(No off-site target yet: `RESTIC_REPOSITORY=/mnt/backup-disk/friday-restic`
on a second disk, and record "off-site pending".)

**The nightly script** — `/usr/local/bin/friday-backup` (0755). Phase 13
branch 6 ships the same script as `deploy/home/friday-backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /srv/friday/friday
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/var/backups/friday/friday-$STAMP.dump.age
COMPOSE="docker compose -f deploy/docker/compose.yml -f deploy/docker/compose.daily.yml"
# 1. Consistent custom-format dump, encrypted before it touches the disk.
$COMPOSE exec -T postgres pg_dump -U jarvis -d jarvis -Fc \
  | age -R /etc/friday-backup/age.pub > "$OUT.part"
mv "$OUT.part" "$OUT"
# 2. The secrets file, encrypted the same way (needed for a restore).
age -R /etc/friday-backup/age.pub < deploy/docker/.env > /var/backups/friday/env-$STAMP.age
# 3. Off-site push and retention.
set -a; . /etc/friday-backup/restic.env; set +a
restic backup --tag friday /var/backups/friday
restic forget --tag friday --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
find /var/backups/friday -name '*.age' -mtime +3 -delete
# 4. Status for preflight (`home_backup_fresh`): no secrets, no paths beyond the file name.
SNAP=$(restic snapshots --tag friday --latest 1 --json | python3 -c 'import json,sys;print(json.load(sys.stdin)[-1]["short_id"])')
printf '{"v":1,"finished_at":"%s","dump":"%s","bytes":%s,"sha256":"%s","restic_snapshot":"%s"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$OUT")" "$(stat -c %s "$OUT")" \
  "$(sha256sum "$OUT" | cut -d' ' -f1)" "$SNAP" > /var/backups/friday/status.json.part
mv /var/backups/friday/status.json.part /var/backups/friday/status.json
```

**The timer** (systemd, runs as `friday` at 03:10 local time):

```ini
# /etc/systemd/system/friday-backup.service
[Unit]
Description=Friday nightly encrypted backup
After=docker.service
[Service]
Type=oneshot
User=friday
ExecStart=/usr/local/bin/friday-backup

# /etc/systemd/system/friday-backup.timer
[Unit]
Description=Friday nightly backup
[Timer]
OnCalendar=*-*-* 03:10:00
Persistent=true
RandomizedDelaySec=10m
[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now friday-backup.timer
sudo systemctl start friday-backup.service && journalctl -u friday-backup -n 50
```

Backups are a host operation the owner configured; no Friday agent, tool
or model can start, change or read them (ADR-017 decision 6). The age
private key is never on the server, so a compromised server cannot read
old backups.

## 9. Restore drill (at least monthly)

A backup that was never restored is a hope. The drill restores the latest
dump into a **scratch** Postgres, never into the live one. Bring the age
private key in only for the drill (a USB stick or paste from the password
manager into a tmpfs file), and remove it afterwards.

```bash
#!/usr/bin/env bash
# /usr/local/bin/friday-restore-drill  (branch 6: deploy/home/friday-restore-drill.sh)
set -euo pipefail
KEY=${1:?path to friday-backup.agekey (on tmpfs)}
cd /srv/friday/friday
LATEST=$(ls -1t /var/backups/friday/friday-*.dump.age | head -1)
# The scratch database joins the stack's own network (`friday_default`) so
# a one-off `web` container can reach it by name; it publishes no port.
docker run -d --rm --name friday-drill-pg --network friday_default -e POSTGRES_USER=jarvis \
  -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=jarvis pgvector/pgvector:pg16
trap 'docker stop friday-drill-pg >/dev/null' EXIT
until docker exec friday-drill-pg pg_isready -U jarvis -d jarvis >/dev/null 2>&1; do sleep 1; done
age -d -i "$KEY" "$LATEST" | docker exec -i friday-drill-pg pg_restore -U jarvis -d jarvis --no-owner
# Schema matches the code, and the node audit chain is intact.
docker compose -f deploy/docker/compose.yml -f deploy/docker/compose.daily.yml run --rm --no-deps \
  -e DATABASE_URL="postgres://jarvis:drill@friday-drill-pg:5432/jarvis" web \
  sh -c "uv run python manage.py migrate --check && uv run python manage.py verify_node_audit_chain"
printf '{"v":1,"drilled_at":"%s","dump":"%s","result":"pass"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$LATEST")" > /var/backups/friday/drill.json
```

Also restore one dump from the **off-site** copy at least once a quarter
(`restic restore latest --tag friday --target /tmp/friday-offsite` and run
the drill against the restored file) so the remote copy is proven too.

After branch 6, preflight reads `status.json` and `drill.json`
(`HOME_BACKUP_STATUS_PATH`, `HOME_RESTORE_DRILL_STATUS_PATH`):
`home_backup_fresh` warns after 26 h and fails after 72 h;
`home_restore_drill_age` warns after 35 days and fails after 90 days.

## 10. Upgrades

1. Read the release notes / merged briefs for migrations and new `.env`
   keys (`.env.example` diff).
2. Run the backup now: `sudo systemctl start friday-backup.service`.
3. `cd /srv/friday/friday && git fetch && git checkout <new tag or main>`.
4. `make up-daily` (web runs `migrate` at start).
5. `make preflight` — every row pass or a known warn. Paired nodes
   reconnect by themselves (exponential backoff, ADR-013 decision 6).
6. A failed upgrade: `git checkout <previous tag>`, restore the dump from
   step 2 with the drill procedure into the **live** database only after
   stopping web/worker/node-gateway, `make up-daily`.

OS and Docker updates: unattended security updates (step 1); reboot when
`/var/run/reboot-required` exists, at a time nobody is talking to Friday.
The LUKS passphrase is asked at boot unless you chose TPM unlock.

## 11. Later: moving the server to a VPS (checklist)

The node protocol does not change (ADR-017 decision 7). Nodes are
re-pointed, not redesigned.

- [ ] Choose the VPS; plan reach through **Tailscale/WireGuard only** —
      no public 443. Install Ubuntu LTS with an encrypted volume for
      `/srv` and Docker's data root.
- [ ] Bring up the same Compose stack (steps 3–4) at the same Friday
      version as the home server.
- [ ] Final backup on the home server; stop web, worker, node-gateway
      there (no writes after the dump).
- [ ] Restore the dump into the VPS Postgres (drill procedure, into the
      live database of the new stack); restore the `.env` from its age
      file — the **same `NODE_SIGNING_KEY`** keeps every node's signing pin.
- [ ] TLS: issue the VPS certificate from the **same private CA** and,
      if the nodes pin the SPKI, from the **same `friday-home.key`**, with
      the new Tailscale name in the SAN. Otherwise every node is re-paired.
- [ ] Update `DJANGO_ALLOWED_HOSTS`, `PREFLIGHT_NODE_TLS_URL`,
      `NODE_TLS_PIN_SHA256` (unchanged if the key is reused) and
      `DEVICE_PAIRING_NETWORKS` (keep the Tailscale range).
- [ ] Home Assistant: reachable from the VPS only through a Tailscale
      subnet router on the home LAN; `HA_BASE_URL` to its LAN address.
- [ ] Re-point each node in its own local settings to the new server URL
      (Mac: the node's settings window behind Touch ID; Linux:
      `friday-node-linux config set server-url …` as the local user).
      Nothing is sent from the server to change it.
- [ ] `make preflight` on the VPS; every node `connected`;
      `verify_node_audit_chain` passes; move the backup timer to the VPS.
- [ ] Wipe the old home server's disk (or keep it powered off as a cold
      spare) — never run two Friday servers against copies of one
      database.

## 12. What preflight checks for the home server (after Phase 13 branch 6)

| Row | Pass | Warn | Fail |
|---|---|---|---|
| `home_lan_only` | every host in `DJANGO_ALLOWED_HOSTS` and the `PREFLIGHT_NODE_TLS_URL` host resolve to private, loopback, ULA or Tailscale CGNAT addresses | URL not set | a public address or a wildcard host |
| `home_tls_pin` | the LAN URL serves the pinned SPKI (the `node_tls_pin` comparison) | pin or URL not set | mismatch or handshake error |
| `home_backup_fresh` | `status.json` ≤ 26 h old and well-formed | 26–72 h, or not configured | > 72 h or malformed |
| `home_restore_drill_age` | `drill.json` ≤ 35 days, `result: pass` | 35–90 days, or not configured | > 90 days, or the last drill failed |
| `home_disk_encryption` | `HOME_FDE_CONFIRMED_ON` set (owner attestation) | not set | — |
