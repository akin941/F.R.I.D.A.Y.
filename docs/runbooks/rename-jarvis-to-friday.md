# Runbook — upgrading an existing install across the Jarvis → Friday rename

On 2026-11-20 the assistant was renamed from **Jarvis** to **Friday**
(styled F.R.I.D.A.Y.) and the code moved to the repository
`akin941/F.R.I.D.A.Y.`. This page is for an install that already runs
(the owner's Mac): what changed, what keeps working on its own, and the
few steps worth doing. Nothing here deletes data.

## TL;DR

```bash
# 1. stop the OLD compose project (its containers are named jarvis-os-*)
docker compose -p jarvis-os -f deploy/docker/compose.yml down
#    (daily stack: add -f deploy/docker/compose.daily.yml)
# 2. point the checkout at the new repository and pull
git remote set-url origin git@github.com:akin941/F.R.I.D.A.Y..git
git pull
# 3. start the renamed stack (runs `make secrets`, migrations and the seed)
make up            # or: make up-daily
# 4. harness hosts only: rebuild the CLI images
make harness-images
```

Then work through the checklist below at leisure; every legacy name is
still accepted for now.

## What keeps working without any action

| Thing | Old | New | Compatibility |
|---|---|---|---|
| Postgres data | volume `jarvis-os_postgres-data`, user/db `jarvis` | unchanged | `compose.yml` pins the volume's old name and keeps the user/db names (they only apply to an empty volume); `config/settings/base.py`'s host default matches |
| Browser sessions + paired devices | cookies `jarvis_access`, `jarvis_refresh`, `jarvis_session`, `jarvis_device` | `friday_*` | the BFF reads the old cookie when the new one is absent, writes only the new names and clears the old ones on the next refresh / device re-mint — paired browsers stay paired |
| Env vars | `JARVIS_BFF_SHARED_SECRET`, `JARVIS_OWNER_PASSWORD`, `JARVIS_CLI_URL`, `JARVIS_CLI_TOKEN` | `FRIDAY_*` | read as a fallback when the `FRIDAY_*` name is unset/empty (`core/legacy_env.py`, `frontend/src/lib/env.ts`); `make secrets` copies `JARVIS_BFF_SHARED_SECRET` to `FRIDAY_BFF_SHARED_SECRET` in `deploy/docker/.env` |
| Prompt templates | `jarvis_system`, `jarvis_router_intent`, `jarvis_memory_extraction`, `jarvis_agent_plan`, … | `friday_*` | migration `ai_core 0006` renames the rows; a template whose active version said "Jarvis" gets a new version saying "Friday" (old versions stay for `ModelUsage` history). `seed_ai_defaults` stays idempotent |
| Vault export | frontmatter `source: jarvis_export`, `jarvis_id` | `friday_export`, `friday_id` | the conservative prune treats both as export-owned; an approved memory's old file is rewritten in the new format on the next export |
| OpenAI-compatible facade | models `jarvis`, `jarvis-lite`; header `X-Jarvis-Conversation-Id` | `friday`, `friday-lite`; `X-Friday-Conversation-Id` | old model ids and the old request header are still accepted; `/v1/models` lists only the new ids and responses carry only the new header |
| CLI config file | `~/.config/jarvis/cli.toml` | `~/.config/friday/cli.toml` | the old file is read when the new one does not exist |
| Deploy target (VPS) | `/opt/jarvis-deploy/jarvis-deploy.sh`, `/etc/jarvis-deploy/`, `/var/lib/jarvis-deploy/` | `friday-deploy.sh`, `/etc/friday-deploy/`, `/var/lib/friday-deploy/` | the installed forced command keeps working untouched — the backend only sends the verb grammar, never a path |

## Checklist

1. **Old containers.** The compose project is now `friday`, so `make down`
   no longer sees the old `jarvis-os-*` containers. Stop them once with
   `docker compose -p jarvis-os -f deploy/docker/compose.yml down` (no `-v`!).
   The data volume is shared by name and is not touched.
2. **Env files.** Rename `JARVIS_*` to `FRIDAY_*` in the repo-root `.env`,
   `frontend/.env.local` and `deploy/docker/.env` (`make secrets` already
   added `FRIDAY_BFF_SHARED_SECRET` there; delete the old line afterwards).
   The fallback will be removed in a later release.
3. **Self-repo guard — do this one.** Add the new remote to
   `HARNESS_SELF_REPO_IDENTIFIERS` (keep the old one while it exists):
   `git@github.com:akin941/F.R.I.D.A.Y..git`. Without it the harness and
   deploy self-modification guard would not recognize the new repository as
   Friday itself.
4. **Re-seed.** `make up` runs `migrate` and `seed_ai_defaults`; on a host
   run use `uv run python manage.py migrate && uv run python manage.py
   seed_ai_defaults` from `backend/`. Check with
   `uv run python manage.py shell -c "from ai_core.models import PromptTemplate as P; print(sorted(P.objects.values_list('name', flat=True)))"`
   — only `friday_*` names should remain.
5. **Harness images.** The images are now `friday-harness-claude-code` /
   `friday-harness-codex-cli` and their output marker is
   `FRIDAY_HARNESS_CLI_OUTPUT_V1`, so images built before the rename can no
   longer report results: `make harness-images` (and `make harness-smoke`).
   If `HARNESS_IMAGE_*` is set in `.env`, update the tags. The default
   network is now `friday-harness-egress`: either
   `docker network create --internal friday-harness-egress`, or keep the old
   network with `HARNESS_DOCKER_NETWORK=jarvis-harness-egress`.
6. **CLI.** Move `~/.config/jarvis/cli.toml` to `~/.config/friday/cli.toml`,
   export `FRIDAY_CLI_URL` / `FRIDAY_CLI_TOKEN` instead of the `JARVIS_`
   names, and run it as `uv run python -m integrations.cli.friday_cli`.
   PATs are unchanged.
7. **OpenAI clients (Open WebUI, …).** Select the `friday` / `friday-lite`
   models (the old ids keep working but are no longer listed) and rename a
   custom `X-Jarvis-Conversation-Id` header to `X-Friday-Conversation-Id`.
8. **Deploy target (optional).** To install the renamed script, on the VPS:
   `mv /etc/jarvis-deploy /etc/friday-deploy`,
   `mv /var/lib/jarvis-deploy /var/lib/friday-deploy`, install
   `deploy/remote/friday-deploy.sh` at `/opt/friday-deploy/` and update the
   `command="..."` in the deploy user's `authorized_keys`
   (`docs/runbooks/deploy-target-setup.md`). Until then the old install keeps
   working. `DEPLOY_SSH_KEY_PATH` is an explicit path and needs no change.
9. **Database name (optional, not recommended now).** The user/db stay
   `jarvis`. Renaming them needs a dump/restore into a fresh volume and
   changing `compose.yml`, `config/settings/base.py` and
   `deploy/docker/gen-secrets.sh` together; it buys nothing functionally.
