# Runbook: setting up a deploy target

Phase 5 branch 6 (ADR-010 decision 7, `docs/prompts/phase-5-deploy-mcp/brief.md`
D10). How to make a VPS app deployable by Friday: a dedicated user, a
forced-command key, a pinned host key, root-owned per-app config, and a
first `status` before the target is switched on.

What Friday can do on the VPS afterwards is exactly four commands, checked
on both ends:

| Verb | Sent as | Effect on the VPS |
|---|---|---|
| `status` | `status <app>` | prints live SHA, branch, origin URL, recorded previous SHA (read-only) |
| `health` | `health <app>` | runs the app's own `HEALTH_CMD` |
| `deploy` | `deploy <app> <sha>` | fetch; refuse unless `<sha>` is an ancestor of `origin/<TRACKED_BRANCH>`; record the live SHA as "previous"; checkout; `BUILD_CMD`; `RESTART_CMD` |
| `rollback` | `rollback <app> <sha>` | refuse unless `<sha>` is the recorded previous SHA; checkout; build; restart; forget "previous" |

Friday never sends anything else, never merges, and never chooses the build
or restart commands: they live in root-owned config on the VPS (brief Q12).

## 0. Before you start

- The runner (`make harness-runner`) is the only process that deploys. It
  consumes the `harness` and `deploy` queues; with it stopped, approved
  deploys wait `pending`.
- `HARNESS_SELF_REPO_IDENTIFIERS` must list this Friday installation's own
  remote(s). With it empty every deploy is refused (fail closed), and a
  target whose repo is Friday itself is refused always.
- Deploys run only **merged** commits: the SHA must already be on the
  target's tracked branch on the git host.

## 1. The runner key (on the runner host)

```bash
ssh-keygen -t ed25519 -N '' -C friday-deploy -f ~/.friday/deploy_key
chmod 600 ~/.friday/deploy_key
```

Set `DEPLOY_SSH_KEY_PATH=~/.friday/deploy_key` (absolute path) in the
runner's environment. Keep it out of any SSH agent: Friday runs `ssh` with
`-F /dev/null`, `IdentitiesOnly=yes`, `IdentityAgent=none` and no
`SSH_AUTH_SOCK`, so an agent is never consulted, and a missing key fails
every deploy with `ssh_key_missing` rather than falling back to another
credential.

## 2. The VPS (as root)

A dedicated account that can do nothing but run the forced command:

```bash
adduser --disabled-password --gecos '' deploy
install -d -o root -g root -m 0755 /opt/friday-deploy /etc/friday-deploy /etc/friday-deploy/apps
install -o root -g root -m 0755 friday-deploy.sh /opt/friday-deploy/friday-deploy.sh   # deploy/remote/friday-deploy.sh
install -d -o deploy -g deploy -m 0750 /var/lib/friday-deploy
```

`~deploy/.ssh/authorized_keys` (owned by `deploy`, mode 0600) gets exactly
one line -- the runner's public key behind the forced command:

```
command="/opt/friday-deploy/friday-deploy.sh",restrict ssh-ed25519 AAAA... friday-deploy
```

`restrict` turns off port/agent/X11 forwarding and the pty. Do not add
`AcceptEnv`/`PermitUserEnvironment` entries for this user. Password login
stays off (`PasswordAuthentication no`).

The app checkout is owned by `deploy`, cloned from the same repository as
the Friday target's `repo_git_url`, with read access to the git host (a
read-only deploy key for `deploy`'s own `~/.ssh`):

```bash
sudo -u deploy git clone git@github.com:acme/adia-web.git /srv/adia-web
```

The per-app config, `/etc/friday-deploy/apps/<app>.conf`, root-owned 0644
(start from `deploy/remote/apps/example.conf`). The file name without
`.conf` is the target's `remote_app` (`[a-z0-9][a-z0-9-]{0,62}`):

```bash
APP_DIR=/srv/adia-web
TRACKED_BRANCH=main
BUILD_CMD="docker compose build --pull"
RESTART_CMD="docker compose up -d --remove-orphans"
HEALTH_CMD="curl -fsS --max-time 5 http://127.0.0.1:8080/healthz"
```

The script refuses a config (or config directory) that is group- or
world-writable or not owned by root. `BUILD_CMD` must not touch the running
service: a failed build restores the previous checkout and nothing is
restarted.

**Residual risk (brief R1).** Whatever `BUILD_CMD`/`RESTART_CMD` can do,
the deploy user can do -- membership in the `docker` group is
root-equivalent on that VPS. That is the owner's decision, installed by the
owner, never by Friday.

## 3. The host key, out of band

Friday pins the host key; there is no trust on first use. Read it on the
VPS itself (console, or an SSH session you already trust):

```bash
cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub
```

and compare the fingerprint (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`)
with what your provider's console shows. Do not copy it from an
`ssh-keyscan` over the network.

## 4. The target in Friday (web, owner)

Create it from the web app (the targets UI arrives with Phase 5 branch 9;
until then `POST /api/v1/deploys/targets` from a web session): name, environment, `ssh_host`/`ssh_port`/`ssh_user`,
the host key from step 3, `remote_app`, `repo_git_url`, `tracked_branch`,
optional `health_url` (a public URL the runner GETs; 2xx = healthy,
redirects are not followed), `health_timeout_seconds` (30-900, default
120), `require_ci_success`, `auto_rollback`.

A new target is always created **disabled**. Changing any configuration field changes its
`config_hash`; a deploy request approved under the old hash is refused
(`target_changed`) instead of running against a configuration nobody
decided on.

`require_ci_success` (default on): until the GitHub connector (Phase 5
branch 5) supplies CI state, every SHA is requested on the manual path with
CI `unknown`, which a target requiring CI refuses (`deploy.ci_required`).
Turn it off for such targets, knowingly.

## 5. The first `status`

Run a status check on the target (`POST /api/v1/deploys/targets/<id>/status`,
web). The runner connects with the pinned key, runs `status <app>` and
checks that the remote's `origin` is the target's repo (canonically, so the
https/ssh spellings match) and not Friday itself. On success the target's
`current_sha` is the live SHA; otherwise `last_status_error` names the
problem:

| `last_status_error` | Meaning |
|---|---|
| `ssh_key_missing` | `DEPLOY_SSH_KEY_PATH` unset or missing on the runner |
| `ssh_failed` | connection, authentication or host-key mismatch (check step 3) |
| `status_failed` | the script refused: unknown app, bad config permissions, `APP_DIR` not a checkout |
| `status_unparseable` | the output did not name this app |
| `repo_mismatch` | the checkout's `origin` is another repository |
| `self_repo`, `self_repo_unconfigured` | the self-modification guard |

Then enable the target.

## 6. Deploying

"Deploy" (web) or the CLI (`POST /api/v1/deploys/requests {target, sha}`)
creates a pending `deploy` approval. The decision is separate and web-only;
the dialog states the full plan, e.g.:

> Deploy `a1b2c3d` to ADİA production (deploy@203.0.113.7, app `adia-web`).
> Current `9f8e7d6`. If health fails within 120 s, Friday rolls back to `9f8e7d6`.

After approval the runner: runs `status` and refuses if the live SHA is not
the one the request saw (`target_drifted`, nothing touched) -> sends
`deploy` -> checks health across the window (the `health` verb, plus the
`health_url` if set):

- two consecutive passes: `succeeded` (a failing `health_url` alone:
  `succeeded_degraded`);
- three consecutive failures, or the window ending without two consecutive
  passes: with `auto_rollback` on, `rollback` to the SHA stated in the
  dialog, then `rolled_back` or `rollback_failed`; with it off,
  `health_failed` and nothing is rolled back.

Only one run per target at a time; approving a second request while one is
running ends it `refused`/`target_busy`. A run can be cancelled only before
the `deploy` command is sent.

## 7. When something goes wrong

- **`unknown_outcome`** (ssh died during `deploy`, or the runner was lost
  after sending it): never retried. Friday forgets the live SHA
  (`current_sha` empty), so the next request is refused until you run a
  status check. Look at the VPS, then run `status` and decide.
- **`rollback_failed`**: the app may be down. Fix it on the VPS; run
  `status`; a further rollback is a new deploy request (of the older merged
  SHA) and a new decision.
- **`remote_busy`**: another deploy of the app holds
  `/var/lib/friday-deploy/<app>.lock`. A lock whose process died is
  reclaimed automatically; remove it by hand only if you are sure nothing is
  running.
- **`deploy_refused`**: the SHA is not on `origin/<TRACKED_BRANCH>`, is
  already live, or the checkout has local modifications.
- **`build_failed`**: the build failed; the previous checkout was restored
  and nothing was restarted.

## 8. Checking the whole path

`make deploy-smoke` builds a throwaway sshd container with the real script
as the forced command and a dummy compose app, and drives status, a
deploy, a forced health failure with its rollback, and a refused
non-merged SHA through the real `ssh` client (needs Docker and a test
database).
