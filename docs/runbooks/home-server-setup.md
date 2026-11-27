# Runbook: the Friday home server (one server, many nodes)

ADR-017 (home hub topology, Accepted 2026-11-26), Phase 13
(`docs/prompts/phase-13-home-hub/brief.md`). How to turn a Linux mini PC on
the home LAN into the one always-on Friday server that every PC pairs with
as a node: encrypted disk, a fixed LAN address and name, the Docker
Compose stack, TLS for the node gateway from a private CA, a LAN-only
firewall, nightly encrypted off-site backups with a restore drill,
upgrades, and the later move to a VPS.

**What the repo ships (Phase 13 branch 7, `feat/p13-home-server-ops`,
2026-11-26).** The stack (`deploy/docker/compose.yml` + `compose.daily.yml`)
plus the home override `deploy/docker/compose.home.yml` (`make up-home`);
the host-side scripts and units in `deploy/home/` --
`friday-backup.sh`, `friday-restore-drill.sh`,
`friday-backup.{service,timer}` and `Caddyfile.example`; and the five
`home_*` preflight rows (`make preflight`) that read the status files the
scripts write. The scripts are shellcheck-clean and tested in CI (the
`home-ops` job) against a real Postgres with a throwaway age key and a
local restic repository: backup, restore drill into a scratch database,
`migrate --check`, `verify_node_audit_chain`, and a tampered dump that
must fail the drill. Nothing here was run on real home hardware yet
(owner step, Mac session M3).

**Final for Phase 13 (`chore/p13-closeout`, 2026-11-26).** Branches 1–7
and the closeout are on the integration branch: rooms and node labels,
"which PC" resolution, per-node presence on the HUD and `/devices`, the
Linux node (`node/friday-node-linux`), the Home Assistant entity
allowlist with media and scene services, the home voice routes, and the
four closeout preflight rows in step 12. The closeout e2e
(`backend/ops/tests/test_home_hub_e2e.py`) runs two PCs against one live
TLS server: the Python reference node standing in for the Mac, and the
packaged Linux node. Your real box, your real PCs and a real restore from
the off-site copy are still the owner's runs (M2/M3, pending owner
evaluation).

**Owner decisions of 2026-11-26 (Phase 13b,
`docs/prompts/phase-13b-home-decisions/brief.md`; ADR-017 amendment,
ADR-018).** Until the home PC is bought, the stack runs on the owner's
MacBook: see **"Interim: server on the MacBook"** at the end of this
runbook. The off-site target is **Cloudflare R2** (step 8). The future home
PC unlocks its disk with the **TPM** (step 1). The Mac server files and
settings (`HOME_SERVER_PLATFORM`, `HOME_BACKUP_OFFSITE_REPOSITORY`,
`deploy/docker/compose.mac.yml`, `make up-mac-server`,
`deploy/home/macos/com.friday.backup.plist`, `deploy/home/restic.env.example`
and the portable backup scripts) shipped with `feat/p13-interim-mac-server`
(2026-11-20).

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
- A Cloudflare R2 bucket for the off-site backups (the decided target,
  owner decision 2026-11-26; step 8). A second local disk is acceptable
  only until the bucket exists; record that as "off-site pending" in step 8.

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

4. **TPM auto-unlock (owner decision 2026-11-26, ADR-017 amendment 3).**
   The box must come back after a power cut without you, so the LUKS
   volume is unlocked by the TPM, bound to PCR 7 (the Secure Boot state),
   with a recovery key that never lives on the server:

   ```bash
   lsblk -f                                    # find the LUKS partition, e.g. /dev/nvme0n1p3
   # 1. A recovery key slot first. It prints the key ONCE: put it in your
   #    password manager and on paper; never save it on this machine.
   sudo systemd-cryptenroll /dev/nvme0n1p3 --recovery-key
   # 2. The TPM slot, bound to PCR 7 only.
   sudo systemd-cryptenroll /dev/nvme0n1p3 --tpm2-device=auto --tpm2-pcrs=7
   sudo systemd-cryptenroll /dev/nvme0n1p3     # lists slots: password, recovery, tpm2
   ```

   Ubuntu's default initramfs (`initramfs-tools`) does not read
   `systemd-cryptenroll` TPM2 tokens. Switch the initramfs generator to
   `dracut` and tell it to use the token: `sudo apt install dracut
   tpm2-tools` (this replaces `initramfs-tools`), add `tpm2-device=auto`
   to the root volume's options in `/etc/crypttab`, then `sudo dracut -f`
   and reboot. The disk must unlock with no prompt; if it asks, the
   passphrase (or the recovery key) still works — fix the TPM slot before
   relying on it. A firmware update or a Secure Boot change alters PCR 7:
   the next boot asks for the passphrase or recovery key; re-enrol the TPM
   slot afterwards (`--wipe-slot=tpm2`, then step 2 again). Keep the
   passphrase slot as well, or remove it once the recovery key is safely
   stored (`--wipe-slot=password`).

   **Residual you accepted (ADR-017 amendment 3):** a thief who takes the
   whole machine and boots it unchanged gets an unlocked disk up to the
   login prompt. The TPM unlock protects a removed or discarded disk and
   a changed boot chain, not the stolen box. Keep the OS login strong,
   the age private key off the server (step 8) and, after a theft, revoke
   every node and rotate `NODE_SIGNING_KEY` from another PC.
   *These commands are written for Ubuntu 24.04 LTS and were not run on
   real hardware here; verify each on the box (Phase 13b owner step).*
5. Updates: `sudo apt update && sudo apt full-upgrade -y`, then enable
   unattended security updates: `sudo dpkg-reconfigure -plow unattended-upgrades`.

Record the date in `deploy/docker/.env` once the stack exists (step 4):
`HOME_FDE_CONFIRMED_ON=YYYY-MM-DD` (read by the `home_disk_encryption`
preflight row through `compose.home.yml`; the container cannot see the
host's disk layout, so this is your attestation).

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
- `TRUSTED_CLIENT_IP_HEADER=X-Real-IP` (Caddy overwrites it with the
  browser's address, `deploy/home/Caddyfile.example`; the BFF forwards the
  client IP so PIN pairing stays LAN-gated, `DEVICE_PAIRING_NETWORKS`).
  Do not use `X-Forwarded-For`: a client can pre-fill it.
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

Once the first backup and the first restore drill have written their
status files (steps 8 and 9), switch to the home override and keep using
it: `make up-home` (= the two files above plus
`-f deploy/docker/compose.home.yml`). It adds the `home_*` settings and
binds the status directory `/var/backups/friday/status/` (only
`status.json` and `drill.json`, never a dump -- not the backup directory
itself) read-only into `web` at `/run/friday-home`, and publishes nothing.
It is a directory mount on purpose: the scripts replace each status file
atomically (rename), and a single-file bind mount would pin the old inode,
so `web` would keep reading a stale status without a restart. It refuses
to start while the directory is missing (`create_host_path: false`; the
first backup creates it), so Docker never creates an empty one in its
place.

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
repository) and bind it to the LAN address only. The repo ships the file
as `deploy/home/Caddyfile.example` (`sudo install -m 0644
deploy/home/Caddyfile.example /etc/caddy/Caddyfile`, then replace the
placeholders); its core:

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
2. Mac: "Mac eşleştir" issues a LAN-only PIN (or, on the host,
   `manage.py issue_pairing_code --node --strong`); follow
   `docs/runbooks/friday-node.md` (the Swift `friday-node`, Mac session).
   Linux PC: follow `node/friday-node-linux/README.md`
   (`friday-node-linux pair --server https://friday-home.local --ca
   <your CA root> --code …`).
   **Known gap:** `Caddyfile.example` sends every path except
   `/ws/node/v1` to the BFF, and the BFF has no `/api/v1/nodes/pair` or
   `/api/v1/auth/nodes/credential/rotate` route, so pairing and credential
   rotation through the home Caddy fail until those two paths reach Django.
   How they do (and which client IP PIN pairing then trusts) is an open
   owner decision recorded in the Phase 13 brief (`chore/p13-closeout`
   owner steps); decide it before pairing the first node on the real box.
3. On `/settings/home`, create the rooms ("Çalışma odası", "Salon"). On
   `/devices`, give each node a unique name, a room and a role, and add
   aliases for short words you will really say ("iş bilgisayarı"). Names
   and aliases are what "which PC" matches ("Masaüstünde testleri
   çalıştır"). A bare "masaüstü" is not a role word; if you call a PC
   that, make it the PC's name or alias.
4. `make preflight`: `node_link` lists every paired node, and
   `nodes_labels` passes once every node has a unique name and, with more
   than one node, a room.

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

**Off-site target: Cloudflare R2 (the default, owner decision 2026-11-26,
ADR-017 amendment 2).** In the Cloudflare dashboard: R2 → create a bucket
(`friday-backup`, no public access, no custom domain) → "Manage API
tokens" → an **Object Read & Write** token **scoped to that bucket only**.
Note the account id, the access key id and the secret; the secret goes
only into the host's restic environment file below — never into
`deploy/docker/.env`, never into any container (Friday cannot read its
backups, ADR-017 decision 6).

The template is `deploy/home/restic.env.example` (names only):
`sudo install -m 0600 -o friday deploy/home/restic.env.example
/etc/friday-backup/restic.env`, then fill it in:

```bash
# /etc/friday-backup/restic.env  (0600, owner friday) -- names as in .env.example
RESTIC_REPOSITORY=s3:https://<account-id>.r2.cloudflarestorage.com/friday-backup
RESTIC_PASSWORD_FILE=/etc/friday-backup/restic.pass
AWS_ACCESS_KEY_ID=<r2 access key id>
AWS_SECRET_ACCESS_KEY=<r2 secret access key>
AWS_DEFAULT_REGION=auto
```

So preflight can check that the last backup really went to R2, put the
**non-secret** repository identifier (the same `s3:https://…/friday-backup`
string, no key) into `deploy/docker/.env` as
`HOME_BACKUP_OFFSITE_REPOSITORY=` (`compose.home.yml` passes it to web).
The backup script records `RESTIC_REPOSITORY` in `status.json` as
`repository`, and `home_backup_fresh` fails when the last backup went
elsewhere (the detail names only the scheme and host), warns when the
status file has no `repository` (an older script), and says "off-site R2"
when they match. Keep the URL free of userinfo (`user:pass@`) and queries
(`?…`): the script refuses to run with either (so no credential can reach
the status file), and preflight fails such a configured value without
echoing it. Preflight never calls the bucket.

```bash
set -a; . /etc/friday-backup/restic.env; set +a; restic init
```

(R2 not set up yet: `RESTIC_REPOSITORY=/mnt/backup-disk/friday-restic`
on a second disk, leave `HOME_BACKUP_OFFSITE_REPOSITORY` empty, and record
"off-site pending" — R2 is still the decided target.)

**The nightly script and timer** ship in the repo
(`deploy/home/friday-backup.sh`, `friday-backup.service`,
`friday-backup.timer`). Install them from the checkout:

```bash
cd /srv/friday/friday
sudo install -m 0755 deploy/home/friday-backup.sh /usr/local/bin/friday-backup
sudo install -m 0644 deploy/home/friday-backup.service deploy/home/friday-backup.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now friday-backup.timer
sudo systemctl start friday-backup.service && journalctl -u friday-backup -n 50
cat /var/backups/friday/status/status.json
```

What one run does (`friday-backup.sh`):

1. `pg_dump -Fc` inside the stack's `postgres` container, piped straight
   into `age -R /etc/friday-backup/age.pub` (the plaintext dump never
   touches the disk) → `friday-<UTC stamp>.dump.age`;
2. `deploy/docker/.env` encrypted the same way → `env-<stamp>.age` (a
   restore needs `NODE_SIGNING_KEY` and the rest);
3. `restic backup --tag friday` of `/var/backups/friday`, then
   `restic forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12
   --prune`, then local `*.age` files older than 3 days are removed;
4. `status/status.json` for preflight, written atomically (temp file,
   then rename, in the `status/` subdirectory `web` mounts read-only):
   `{"v":1,"finished_at":…,"dump":…,"bytes":…,"sha256":…,"restic_snapshot":…,"repository":…}`
   -- no secret, no path beyond the dump's file name and the repository
   identifier.

It refuses to run when `age.pub` holds a private key (`AGE-SECRET-KEY-`)
or `RESTIC_REPOSITORY` carries userinfo or a query, needs bash ≥ 4.4,
takes a `flock` (or, where there is none, as on macOS, a
`.friday-backup.lock.d` lock directory holding its PID, taken over only
when that process is gone) so a manual start and the timer never overlap,
and leaves
neither a partial file nor a new `status.json` when any step fails (so
`home_backup_fresh` ages into warn and fail). Every location is an
environment variable with the defaults above (`FRIDAY_REPO_DIR`,
`FRIDAY_BACKUP_DIR`, `FRIDAY_STATUS_DIR` (default `$FRIDAY_BACKUP_DIR/status`), `FRIDAY_AGE_RECIPIENTS`, `FRIDAY_RESTIC_ENV`,
`FRIDAY_ENV_FILE`, `FRIDAY_KEEP_LOCAL_DAYS`, `FRIDAY_RESTIC_KEEP`, and
`FRIDAY_PATH`, which replaces `PATH` for launchd on the Mac; change
them with `sudo systemctl edit friday-backup.service`). The timer runs at
03:10 local time with `Persistent=true` (a missed run happens at the next
boot).

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
cd /srv/friday/friday
sudo install -m 0755 deploy/home/friday-restore-drill.sh /usr/local/bin/friday-restore-drill
# The key only for the drill, on tmpfs:
install -m 0600 /dev/null /run/user/$(id -u)/friday-backup.agekey   # then paste the key in
friday-restore-drill /run/user/$(id -u)/friday-backup.agekey
shred -u /run/user/$(id -u)/friday-backup.agekey
cat /var/backups/friday/status/drill.json
```

What it does (`friday-restore-drill.sh`): picks the newest
`friday-*.dump.age` (or the file given as the second argument); when
`status/status.json` names that dump, its sha256 must match (otherwise --
an older dump, one from the off-site copy -- it logs that the comparison
was skipped, and age's MAC is the tamper check); starts a throwaway
`pgvector/pgvector:pg16` container on the stack's network (`friday_default`,
no published port, a random password); streams `age -d | pg_restore
--exit-on-error` into it; runs `migrate --check` and
`verify_node_audit_chain` in a one-off `web` container pointed at it; stops
the container and writes `status/drill.json`
(`{"v":1,"drilled_at":…,"dump":…,"result":"pass"|"fail"}`). Any failure --
a sha256 mismatch, an age MAC error on a tampered file, a restore error, a
schema or chain check -- writes `result: fail` and exits non-zero, and
`home_restore_drill_age` fails until a drill passes again.
(`FRIDAY_DRILL_MODE=direct` with `FRIDAY_DRILL_DATABASE_URL` restores into
an existing **empty** database with the host's `pg_restore` instead -- the
CI test uses it; the script refuses a database that already has tables.)

After the first backup and drill: `make up-home` (step 4) and
`make preflight`.

Also restore one dump from the **off-site** copy at least once a quarter
so the remote copy is proven too:

```bash
set -a; . /etc/friday-backup/restic.env; set +a
restic restore latest --tag friday --target /tmp/friday-offsite
friday-restore-drill /run/user/$(id -u)/friday-backup.agekey \
  "$(ls /tmp/friday-offsite/var/backups/friday/friday-*.dump.age | tail -1)"
rm -rf /tmp/friday-offsite
```

Preflight reads `status/status.json` and `status/drill.json`, mounted at
`/run/friday-home/` (`HOME_BACKUP_STATUS_PATH`,
`HOME_RESTORE_DRILL_STATUS_PATH`, set by `compose.home.yml`):
`home_backup_fresh` warns after 26 h and fails after 72 h;
`home_restore_drill_age` warns after 35 days and fails after 90 days.

## 10. Upgrades

1. Read the release notes / merged briefs for migrations and new `.env`
   keys (`.env.example` diff).
2. Run the backup now: `sudo systemctl start friday-backup.service`.
3. `cd /srv/friday/friday && git fetch && git checkout <new tag or main>`.
4. `make up-home` (web runs `migrate` at start).
5. `make preflight` — every row pass or a known warn. Paired nodes
   reconnect by themselves (exponential backoff, ADR-013 decision 6).
6. A failed upgrade: `git checkout <previous tag>`, restore the dump from
   step 2 with the drill procedure into the **live** database only after
   stopping web/worker/node-gateway, `make up-home`.

OS and Docker updates: unattended security updates (step 1); reboot when
`/var/run/reboot-required` exists, at a time nobody is talking to Friday.
The TPM unlocks the disk at boot (step 1); after a firmware or Secure Boot
update it asks for the passphrase or recovery key once — re-enrol the TPM
slot then.

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

## 12. What preflight checks for the home server (`check_home`)

| Row | Pass | Warn | Fail |
|---|---|---|---|
| `home_lan_only` | every host in `DJANGO_ALLOWED_HOSTS` and the `PREFLIGHT_NODE_TLS_URL` host resolve to private, loopback, ULA or Tailscale CGNAT addresses | URL not set | a public address or a wildcard host |
| `home_tls_pin` | the LAN URL serves the pinned SPKI (the `node_tls_pin` comparison) | pin or URL not set | mismatch or handshake error |
| `home_backup_fresh` | `status.json` ≤ 26 h old and well-formed; with `HOME_BACKUP_OFFSITE_REPOSITORY` set, its `repository` equals it ("off-site R2" for an R2 host) | 26–72 h, not configured, or (with the off-site setting) a status file that recorded no `repository` | > 72 h, malformed, the last backup went to another repository, or a configured/recorded repository with userinfo (`@`) or a query (`?`) — never echoed |
| `home_restore_drill_age` | `drill.json` ≤ 35 days, `result: pass` | 35–90 days, or not configured | > 90 days, or the last drill failed |
| `home_disk_encryption` | `linux`: `HOME_FDE_CONFIRMED_ON` set (owner LUKS attestation) | `linux`: not set; `macos`: **always** — "FileVault owner-confirmed on <date>; no LUKS/TPM on macOS (interim)" or "FileVault not confirmed" | — |
| `home_server_platform` | `HOME_SERVER_PLATFORM=linux` (the default) | `macos`: interim server (ADR-017 amendment 1), Friday is down while the Mac sleeps | any other value |

A configured status path whose file is missing or malformed (wrong `v`, a
timestamp that is not UTC or lies in the future, a dump name that is not
`friday-<stamp>.dump.age`) fails. `home_lan_only` treats an unresolvable
`.local` name as link-local (mDNS does not resolve inside a container) and
warns on any other name it cannot resolve; the thresholds are the
`HOME_BACKUP_*_HOURS` / `HOME_DRILL_*_DAYS` settings
(`deploy/docker/.env.example`).

The Phase 13 closeout adds `check_home_control` (read-only; one read of
Home Assistant's states is its only outside contact):

| Row | Pass | Warn | Fail |
|---|---|---|---|
| `nodes_labels` | every non-revoked node has a unique name; rooms are set when there is more than one node | no node paired, or a node without a room in a multi-node home | two nodes with the same name |
| `ha_entities` | at least one enabled allowlisted entity, each inside the bridge's code maximum and present in Home Assistant | none allowlisted, HA not configured, an entity missing in HA or narrowed away by `HA_ALLOWED_DOMAINS` | an allowlisted entity outside the code maximum (a `NEVER_DOMAINS` or read-only domain, or a service the bridge does not offer), or HA unreachable |
| `linux_node_vendor` | `node/friday-node-linux/vendor.lock` matches this backend's sources and the vendored copies | no `node/friday-node-linux` checkout on this host | drift (run `scripts/vendor.py` there) |
| `node_protocol_frozen` | the node protocol files and `node/friday-voice/conformance.lock` hash to the Phase 12 closeout values (Phase 13 changes no protocol) | no `node/friday-voice` checkout (only the node half is checked) | any change since the Phase 12 closeout |

The backend image ships no `node/` directory, so in the container
`linux_node_vendor` and half of `node_protocol_frozen` warn. Set
`PREFLIGHT_LINUX_NODE_DIR` / `PREFLIGHT_FRIDAY_VOICE_DIR` to a checkout to
check them there, or run `make preflight` from the repository checkout.

The Phase 13b closeout (`chore/p13b-closeout`, 2026-11-20) adds
`check_home_direct` (read-only: no Home Assistant request, no model call,
nothing written) for ADR-018's direct media class. `home_server_platform`
(Phase 13b branch 2) is the last `check_home` row above.

| Row | Pass | Warn | Fail |
|---|---|---|---|
| `ha_direct_media` | both kill switches off, at least one flagged entity, each one enabled, a `media_player` with at least one direct-eligible service ticked, and the closed set's digest equal to ADR-018's recorded value | the class is off by the environment (`HA_DIRECT_MEDIA_KILL_SWITCH=true` or a `HA_DIRECT_RATE_*` limit of 0) or by the owner's "Doğrudan medyayı kapat"; Home Assistant not configured in the owner's personal workspace (manifest, binding or `ha_call_service` pin); nothing flagged (owner step: flag a TV on `/connectors`); a flagged entity disabled in the allowlist | a flagged entity outside the class (not a `media_player`, no direct-eligible service, or outside a personal workspace — `HomeEntity.save()` refuses all three, so this means a write went around it), or the closed set changed without an ADR-018 amendment (digest mismatch) |

The detail names the state (`on`, `off_env`, `off_owner`), the flagged
count, both rate limits and the volume maximum, never a token, a URL or a
Home Assistant state. On the interim Mac a macOS-declared run with
R2-recorded status files has no `fail` row and `ha_direct_media` passes
once a TV is flagged (`backend/ops/tests/test_home_direct_e2e.py`).

---

## Interim: server on the MacBook

Owner decision of 2026-11-26 (Phase 13b D1, ADR-017 amendment 1): until the
home PC exists, the same Compose stack runs on the owner's MacBook. **Friday
is down whenever the Mac sleeps, travels or is off**, and every other PC
loses Friday with it; nodes fail closed (ADR-013 decision 6) and nothing is
queued. Everything else in ADR-017 holds: LAN only, TLS with a pin or the
private CA for every node, nightly encrypted backups to R2, Postgres as the
only source of truth. The files and settings below shipped with
`feat/p13-interim-mac-server` (2026-11-20).

### I1. Prerequisites on the Mac

- macOS on Apple silicon, at least 16 GB RAM, 50 GB free disk.
- **FileVault on** (System Settings → Privacy & Security → FileVault).
  This is the Mac's full-disk encryption; a container cannot see it, so
  you attest it: `HOME_FDE_CONFIRMED_ON=YYYY-MM-DD` in
  `deploy/docker/.env` once `fdesetup status` says "FileVault is On". With
  `HOME_SERVER_PLATFORM=macos` (set by `deploy/docker/compose.mac.yml`)
  the `home_disk_encryption` row reports this as an **owner-confirmed
  warning** ("FileVault owner-confirmed on <date>; no LUKS/TPM on macOS
  (interim)"), never a pass and never a failure: there is no LUKS or TPM
  on a Mac, and the interim server is meant to be replaced.
- A container runtime: **OrbStack** or **Docker Desktop** (either runs the
  stack unchanged; give it at least 8 GB RAM). In Docker Desktop's
  Settings → Resources → File sharing, share the repository and the backup
  directory (I5).
- Homebrew: `brew install bash age restic caddy` (`bash` ≥ 4.4 for the
  backup scripts; macOS ships 3.2), plus `uv` and Node as in
  `docs/runbooks/mac-session-handoff.md` A2.
- Energy settings: on mains power, "Prevent automatic sleeping when the
  display is off" (System Settings → Displays/Battery → Options) keeps
  Friday up while the lid is open. A closed lid without an external
  display sleeps the Mac: Friday is then down by design.

### I2. The stack

```bash
git clone <your Friday repository URL> ~/code/friday && cd ~/code/friday
git checkout main                       # or integ/phase-13 before the owner's merge
make secrets                            # deploy/docker/.env (0600)
```

`deploy/docker/.env`, in addition to step 4's settings:

- `DJANGO_ALLOWED_HOSTS=<mac-name>.local,<mac LAN IP>,localhost,127.0.0.1,web`
  (System Settings → General → Sharing shows the `.local` name; give the
  Mac a DHCP reservation on the router as in step 2).
- `HOME_BACKUP_HOST_DIR=/Users/<you>/FridayBackups` (its `status/`
  subdirectory must exist before the first `up`: run the first backup, I5).
  `HOME_SERVER_PLATFORM=macos` comes from `compose.mac.yml`; setting it in
  `.env` as well is harmless.
- `HOME_FDE_CONFIRMED_ON=` (I1) and, once R2 is set up,
  `HOME_BACKUP_OFFSITE_REPOSITORY=` (step 8).

Start it with the Mac server target: `make up-mac-server` = the daily
stack, the home override, the loopback TLS overlay
(`compose.local-tls.yml`, `make local-tls` runs first) and
`compose.mac.yml`. Every port stays on `127.0.0.1` (the compose test
enforces it): with Docker Desktop and OrbStack a `127.0.0.1:` mapping is
reachable from the Mac only. `make preflight` then shows
`home_server_platform` and `home_disk_encryption` as warnings and no
failure caused by macOS (step 12).

### I3. This Mac's own node: the loopback TLS path

The Mac's `friday-node` pairs with the server **on the same machine**
through the loopback-only TLS proxy from `feat/p9-node-local-pairing`
(`deploy/docker/compose.local-tls.yml`, `Caddyfile.local`): Caddy listens
on `127.0.0.1:8443` only, forwards exactly `/api/v1/nodes/pair`,
`/api/v1/auth/nodes/credential/rotate` and `/ws/node/v1`, answers 404 to
everything else, and installs nothing into any trust store.

1. `make local-tls` once: a self-signed P-256 certificate in
   `deploy/docker/local-tls/` (gitignored); it prints the SPKI pin. Put the
   printed `NODE_TLS_PIN_SHA256=…` line into `deploy/docker/.env`.
2. Issue a **strong** node code (PIN codes are refused on this path; the
   proxy is not the BFF): `docker compose -f deploy/docker/compose.yml exec
   web python manage.py issue_pairing_code --node --strong`.
3. FridayNode menu → "Eşleştir…": server `https://127.0.0.1:8443`, the
   code, and the printed pin in "Sertifika pini"
   (`docs/runbooks/friday-node.md`, "A node on this Mac only").
4. `make preflight`: `node_link` shows the Mac node connected and
   `node_tls_pin` passes against the loopback proxy.

### I4. Other PCs on the LAN (optional while the server is a Mac)

Other PCs reach the Mac through a LAN-bound proxy with the private CA, the
same as step 5 on the home PC:

- Make the CA and a server key and certificate as in step 5, with
  `subjectAltName=DNS:<mac-name>.local,IP:<mac LAN IP>`; keep the CA root
  key off the Mac (another PC or an encrypted USB stick).
- Run Homebrew's Caddy on the **host** (not in Docker) with
  `deploy/home/Caddyfile.example`, `bind <mac LAN IP>`, the TLS files under
  `$(brew --prefix)/etc/caddy/tls/`, and `brew services start caddy`.
  The node-pairing route through this proxy is the open Phase 13 owner
  decision (step 7 "Known gap"); until it is decided, pair a LAN PC through
  an SSH port-forward to the Mac's loopback (`ssh -L 8443:127.0.0.1:8443
  <mac>`) and the loopback path of I3.
- Firewall: System Settings → Network → Firewall on; allow incoming
  connections for Caddy only. No port forwarding on the router, no UPnP,
  no tunnel (ADR-017 decision 3). From another PC,
  `nc -vz <mac LAN IP> 8001` and `… 8002` must fail; `… 443` succeeds.
- Set `PREFLIGHT_NODE_TLS_URL=https://<mac-name>.local/ws/node/v1` and its
  `NODE_TLS_PIN_SHA256` so `home_tls_pin` checks the LAN listener (the
  loopback node keeps the pin it paired with).

### I5. Backups while the Mac is awake

The same scripts as steps 8–9, run by `launchd` instead of systemd:

- Keys and R2 exactly as step 8 (the age key made on another PC; only the
  public key on the Mac). Keep the restic environment file and the age
  public key under `/Users/<you>/.config/friday-backup/` (directory 0700,
  files 0600) and point the script at them with `FRIDAY_RESTIC_ENV` and
  `FRIDAY_AGE_RECIPIENTS`; the backup directory is
  `FRIDAY_BACKUP_DIR=/Users/<you>/FridayBackups` (shared with the
  container runtime, I1) and `FRIDAY_REPO_DIR=/Users/<you>/code/friday`.
- The restic environment file from the template:
  `install -m 0600 deploy/home/restic.env.example
  ~/.config/friday-backup/restic.env`, filled in as step 8.
- Schedule it with the LaunchAgent `deploy/home/macos/com.friday.backup.plist`
  (03:10 daily; Homebrew `/opt/homebrew/bin/bash` runs
  `deploy/home/friday-backup.sh` from the checkout; its
  `EnvironmentVariables` hold only the `FRIDAY_*` paths, including
  `FRIDAY_PATH`, because launchd's own `PATH` has neither Homebrew nor the
  Docker CLI; logs in `~/Library/Logs/friday-backup.log`). Its paths use
  the placeholder `/Users/USERNAME`:

  ```bash
  mkdir -p ~/Library/LaunchAgents ~/Library/Logs
  sed "s|/Users/USERNAME|$HOME|g" deploy/home/macos/com.friday.backup.plist \
    > ~/Library/LaunchAgents/com.friday.backup.plist
  plutil -lint ~/Library/LaunchAgents/com.friday.backup.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.friday.backup.plist
  launchctl kickstart gui/$(id -u)/com.friday.backup     # one manual run
  tail -n 20 ~/Library/Logs/friday-backup.log; cat ~/FridayBackups/status/status.json
  ```

  (Docker Desktop puts `docker` in `/usr/local/bin`, OrbStack in
  `~/.orbstack/bin`; both are in the plist's `FRIDAY_PATH`.) launchd
  runs a calendar job that was missed while the Mac slept **once, at the
  next wake**, so a night with the lid closed becomes a morning backup.
  `home_backup_fresh` still warns after 26 h and fails after 72 h: a long
  trip with the Mac off shows up as a stale backup, on purpose.
- The scripts need Homebrew `bash` (≥ 4.4; with macOS's 3.2 they stop with
  "needs bash >= 4.4") and work with the macOS tools: `shasum -a 256` when
  there is no `sha256sum`, `wc -c` instead of GNU `stat -c`, and a
  `.friday-backup.lock.d` lock directory (with a stale-PID check) when
  there is no `flock` — tested under a PATH shim of that tool set
  (`backend/ops/tests/test_home_backup_portable.py`). No GNU coreutils
  needed.
- Restore drill: monthly, as step 9, with the age private key brought in
  only for the drill (a file under `$TMPDIR`, removed afterwards).

### I6. Later: moving from the Mac to the home PC

The same path as the VPS move (step 11), but onto the home PC:

- [ ] Set up the home PC with steps 0–6 (Ubuntu, LUKS with the TPM unlock
      of step 1, a fixed address, Docker, the stack, Caddy with the
      private CA, ufw).
- [ ] Final backup on the Mac (run `friday-backup` by hand); stop web,
      worker and node-gateway there.
- [ ] Restore the dump into the home PC's Postgres (the drill procedure,
      into the live database of the new stack) and the `.env` from its
      age file — the **same `NODE_SIGNING_KEY`** keeps every node's
      signing pin.
- [ ] TLS: certificates for the home PC from the **same private CA**
      (LAN PCs that pin the CA keep working). The Mac's own node paired
      over the loopback self-signed pin of I3: it must be **re-pointed**
      to `https://friday-home.local` with the new pin, or re-paired (Mac
      session, Phase 13 M1 re-point).
- [ ] Re-point each LAN node to the home PC in its own local settings
      (Linux: `friday-node-linux config set server-url …`). Nothing is sent
      from the server to change it.
- [ ] Move the backup schedule to the home PC (systemd timer, step 8) and
      unload the LaunchAgent on the Mac (`launchctl bootout …`).
- [ ] `make preflight` on the home PC; every node `connected`;
      `verify_node_audit_chain` passes; set `HOME_SERVER_PLATFORM=linux`
      (or remove it) and `HOME_FDE_CONFIRMED_ON` to the LUKS date.
- [ ] Stop the stack on the Mac for good (`make down`) and delete its
      Docker volumes once the home PC has a passing restore drill — never
      run two Friday servers against copies of one database.

*Nothing in this section was run on a real Mac here (the implementation
container is Linux): the Mac session runs it (Phase 13b, "Mac session
(deferred)" M1–M2) and fixes any step that differs.*
