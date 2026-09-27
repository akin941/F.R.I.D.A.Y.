"""Base settings — everything configurable comes from the environment (12-factor)."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(DJANGO_DEBUG=(bool, False))
# Read .env from the repo root if present (local development convenience).
environ.Env.read_env(BASE_DIR.parent / ".env")

# `or` fallbacks: a copied .env.example leaves vars set-but-empty; treat empty as unset.
SECRET_KEY = env("DJANGO_SECRET_KEY", default="") or "dev-insecure-key-change-me-0123456789abcdef"
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = [h for h in env.list("DJANGO_ALLOWED_HOSTS", default=[]) if h] or [
    "localhost",
    "127.0.0.1",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "corsheaders",
    # Jarvis kernel
    "core",
    "accounts",
    "workspaces",
    "audit",
    "ai_core",
    "assistant",
    "memory",
    "knowledge",
    "tasks",
    "approvals",
    "tools",
    # "skills" must be listed after "tools": SkillsConfig.ready() validates
    # every skill's tool_request references against tools.registry at boot
    # time, which only sees every tool if ToolsConfig.ready() already ran
    # (Django calls ready() in INSTALLED_APPS order) -- MVP 8 §2, brief.
    "skills",
    "agents",
    "integrations",
    "harness",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "core.middleware.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Postgres is the source of truth (ADR-001); sqlite fallback was removed in MVP 1.
# Default points at the Compose postgres (host port 5433).
_DEFAULT_DATABASE_URL = "postgres://jarvis:jarvis@localhost:5433/jarvis"
DATABASES = {"default": env.db_url_config(env("DATABASE_URL", default="") or _DEFAULT_DATABASE_URL)}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# TODO(MVP 3): MinIO/S3 storage backend via django-storages (files app).
MINIO_ENDPOINT = env("MINIO_ENDPOINT", default="")
MINIO_ACCESS_KEY = env("MINIO_ACCESS_KEY", default="")
MINIO_SECRET_KEY = env("MINIO_SECRET_KEY", default="")
MINIO_BUCKET = env("MINIO_BUCKET", default="jarvis")

# Celery
REDIS_URL = env("REDIS_URL", default="") or "redis://localhost:6380/0"
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
# Environment-backed (MVP 2 §4): default off, so `make up`'s worker container
# genuinely processes tasks out-of-process. The backend test suite and the
# e2e CI job set both of these true (see backend/conftest.py) so a
# transaction.on_commit dispatch runs the task inline instead of requiring a
# live broker; EAGER_PROPAGATES=true makes an eager task's own exceptions
# surface in tests rather than being swallowed.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = env.bool("CELERY_TASK_EAGER_PROPAGATES", default=False)

# Harness runner queue (Phase 3 branch 8, ADR-008 decision 7's runner
# abstraction): every `harness.tasks.*` task -- job execution AND the
# recovery scans, which also call `docker` -- is routed to its own queue,
# consumed only by a worker running on a registered runner host that has
# Docker and the harness images (first runner: the owner's Mac, `make
# harness-runner`). The compose worker has no Docker socket and consumes
# only the default queue, so it can never pick up a harness task and fail
# it. With no runner running, harness jobs simply wait `pending`.
HARNESS_CELERY_QUEUE = env("HARNESS_CELERY_QUEUE", default="harness")
CELERY_TASK_ROUTES = {"harness.tasks.*": {"queue": HARNESS_CELERY_QUEUE}}

# --- API / auth ---

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # "chat" scope caps provider-cost endpoints (assistant message send); see
    # assistant/views.py. TODO(MVP 1): anon/user throttles for auth endpoints.
    "DEFAULT_THROTTLE_RATES": {"chat": "30/min"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "UPDATE_LAST_LOGIN": True,
}

# Dev signup switch — prod default is False (see dev.py for the dev default).
SIGNUP_ENABLED = env.bool("SIGNUP_ENABLED", default=False)

CORS_ALLOWED_ORIGINS = [o for o in env.list("CORS_ALLOWED_ORIGINS", default=[]) if o] or [
    "http://localhost:3001"
]

# --- AI gateway (ADR-003) ---

# When on, the deterministic fake provider is selectable (dev/tests/e2e).
# It is filtered out of provider selection entirely when off.
AI_FAKE_PROVIDER = env.bool("AI_FAKE_PROVIDER", default=False)
AI_TIMEOUT_CONNECT = env.int("AI_TIMEOUT_CONNECT", default=10)
AI_TIMEOUT_READ = env.int("AI_TIMEOUT_READ", default=60)
AI_MAX_RETRIES = env.int("AI_MAX_RETRIES", default=2)
AI_RETRY_BACKOFF_SECONDS = env.float("AI_RETRY_BACKOFF_SECONDS", default=0.5)
# Tier routing skeleton (phase 0, MASTER_PLAN §J): purpose -> tier name
# ("cheap"/"mid"/"strong", ai_core.ModelTier). Empty = pure priority order,
# byte-for-byte the pre-phase-0 gateway behavior. Deliberately a settings
# dict, not env/DB, until it needs runtime editing (phase-0 brief §4).
AI_PURPOSE_TIERS = {}

# --- Knowledge/RAG (MVP 3) ---

# Chunking (knowledge.chunking): fixed-size character chunks with overlap,
# paragraph/sentence boundary aware where possible.
KNOWLEDGE_CHUNK_SIZE = env.int("KNOWLEDGE_CHUNK_SIZE", default=1000)
KNOWLEDGE_CHUNK_OVERLAP = env.int("KNOWLEDGE_CHUNK_OVERLAP", default=150)
# Upload size cap for pasted_text/file_upload (branch 2 sources only).
KNOWLEDGE_MAX_UPLOAD_BYTES = env.int("KNOWLEDGE_MAX_UPLOAD_BYTES", default=1_000_000)

# Django's DATA_UPLOAD_MAX_MEMORY_SIZE gates the raw JSON request body before
# any view code runs. JSON `\uXXXX`-escaping can inflate non-ASCII pasted text
# up to ~3x its decoded UTF-8 size on the wire, so a document within
# KNOWLEDGE_MAX_UPLOAD_BYTES could otherwise be rejected by Django's global
# body-size check before create_pasted_text_document's definitive decoded-size
# check ever runs (review finding). Scale the ceiling off the actual accepted
# content size — with headroom for the escaping ratio plus the request's other
# fields — rather than hand-picking an unrelated number.
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int(
    "DATA_UPLOAD_MAX_MEMORY_SIZE", default=max(2_621_440, KNOWLEDGE_MAX_UPLOAD_BYTES * 4)
)

# `url` source ingestion (branch 3): each redirect hop is independently
# re-resolved and re-validated (knowledge.url_fetch), never followed natively
# by the HTTP client — capped so a malicious or misconfigured server can't
# force an unbounded chain of hops.
KNOWLEDGE_URL_MAX_REDIRECTS = env.int("KNOWLEDGE_URL_MAX_REDIRECTS", default=3)

# Obsidian vault (human-readable memory mirror — ADR-005). Filesystem export
# is the `vault_export` gated tool (MVP 5 §3, tools/export.py) — the first
# write/delete action to go through the approval gate (CLAUDE.md rule 4).
# Tier 1 (tools.vault_paths.validate_vault_root_string) validates this
# string with zero filesystem access; tier 2 (only meaningful in the worker
# container, where the vault is actually bind-mounted) checks it exists as
# a real directory.
OBSIDIAN_VAULT_PATH = env("OBSIDIAN_VAULT_PATH", default="")

# Approval gate (ADR-004, MVP 5 §1). A `pending` ApprovalRequest older than
# this is expired — by the periodic sweep (approvals.tasks) and, as a
# backstop, by the decision endpoints themselves regardless of the sweep's
# timing.
APPROVAL_REQUEST_TTL_HOURS = env.int("APPROVAL_REQUEST_TTL_HOURS", default=24)

# Dev-only escape hatch for the requester != decider rule ADR-004 requires
# --- Agent runtime (MVP 7) ---

# Plan step-count bound (brief §1, user-mandated brake #1): a planner call
# returning more steps than this rejects the whole plan (Agent.status =
# failed) rather than truncating it. Proposed default, adjustable.
AGENT_MAX_PLAN_STEPS = env.int("AGENT_MAX_PLAN_STEPS", default=10)

# Per-step execution window (brief §3, brake #2a) -- the Celery step-advance
# task's own soft/hard time limit, covering one step's actual work (a read
# step's embed+summarize calls; a gate-worthy step's local transition).
# Not the human approval/confirmation wait, which is unbounded up to
# ApprovalRequest's own expiry sweep.
AGENT_STEP_TIMEOUT_SECONDS = env.int("AGENT_STEP_TIMEOUT_SECONDS", default=60)

# Consecutive-failure halt (brief §3, brake #2b): zero internal retries --
# a step fails once and counts toward this bound, full stop.
AGENT_MAX_CONSECUTIVE_FAILURES = env.int("AGENT_MAX_CONSECUTIVE_FAILURES", default=2)

# Resource/token budget (brief §3, brake #2c): a per-Agent gateway-call
# ceiling (Agent.gateway_calls_used), checked before starting a NEW read
# step -- a step already in flight is allowed to finish, so this is a soft
# start-condition ceiling with a deliberate bounded one-step overshoot, not
# a hard never-exceed cap.
AGENT_MAX_GATEWAY_CALLS = env.int("AGENT_MAX_GATEWAY_CALLS", default=20)

# Recovery scan (Codex round-2 fix, branch 3): mirrors memory/knowledge/
# tools' identical recovery-grace convention. A `read`-kind AgentStep still
# `running` past this long since its own last update was never finalized by
# its own worker (crash, OOM-kill, or Celery's hard `time_limit` SIGKILLing
# the child before the soft-limit handler could run) -- must exceed
# AGENT_STEP_TIMEOUT_SECONDS + the Celery hard-limit buffer (asserted in
# agents/tasks.py) or the scan could reclaim a step still legitimately
# in flight.
AGENT_STEP_RECOVERY_GRACE_SECONDS = env.int("AGENT_STEP_RECOVERY_GRACE_SECONDS", default=180)

# An Agent stuck `running` this long since its own last update, with no
# currently-running step, suggests its on_commit dispatch to the next
# pending step was lost (e.g. broker down at commit time) -- mirrors
# knowledge's/tools' identical `PENDING_RECOVERY_GRACE_SECONDS`.
AGENT_DISPATCH_RECOVERY_GRACE_SECONDS = env.int("AGENT_DISPATCH_RECOVERY_GRACE_SECONDS", default=60)

# A third distinct "lost on_commit callback" gap (Codex round-4 fix): the
# approval-resume signal (agents/signals.py) also registers its resume via
# an in-memory `transaction.on_commit` callback -- if the process exits
# after the ApprovalRequest's own decision commits but before that callback
# runs, the decision is durable but the Agent/AgentStep are never told
# about it. An AgentStep still `blocked_on_approval` this long after its
# linked ApprovalRequest was actually decided (`decided_at`) means that
# callback was lost -- reconciled by calling `resume_after_approval_decision`
# directly.
AGENT_APPROVAL_RESUME_RECOVERY_GRACE_SECONDS = env.int(
    "AGENT_APPROVAL_RESUME_RECOVERY_GRACE_SECONDS", default=60
)

# --- Harness runner (Phase 2 branch 2, brief §2) ---

# Runtime brake (brief §2, brake #1) -- the Celery job task's own soft time
# limit, covering the container's whole run (clone + CLI invocation).
HARNESS_MAX_JOB_SECONDS = env.int("HARNESS_MAX_JOB_SECONDS", default=1800)

# Cost/budget ceiling (brief §2, brake #2), checked against the CLI's own
# reported cost after the run completes -- an agent without a reliable
# reported cost figure is simply not offered as a choice at all (brief,
# Codex round-1 MEDIUM), so this ceiling is never silently unenforced.
# A plain string, not a float -- consumers wrap it in `Decimal(str(...))`
# so cost comparisons never pick up binary-float rounding error.
HARNESS_MAX_JOB_COST = env("HARNESS_MAX_JOB_COST", default="1.00")

# Kill switch (brief §2, brake #3): grace period between `docker stop`
# (SIGTERM, lets the CLI exit cleanly) and `docker kill` (SIGKILL) when
# tearing a container down, whether via cancellation or brake enforcement.
HARNESS_KILL_GRACE_SECONDS = env.int("HARNESS_KILL_GRACE_SECONDS", default=10)

# How often the owning Celery task polls `HarnessJob.cancel_requested`
# while its container is running (brief, Codex round-4 MEDIUM fix: a
# non-blocking wait loop around the whole container lifetime, not just a
# check "between phases" -- a cancel during the one long-running phase,
# the CLI invocation itself, must be observed before it exits on its own).
HARNESS_CANCEL_POLL_SECONDS = env.int("HARNESS_CANCEL_POLL_SECONDS", default=2)

# The Celery task's own soft_time_limit must exceed HARNESS_MAX_JOB_SECONDS
# by this much (Codex code-review P2, branch 2 round 5: an earlier draft
# set soft_time_limit == HARNESS_MAX_JOB_SECONDS exactly, racing the wait
# loop's own internal timeout check -- if Celery's limit fired while the
# loop was still mid-teardown/capture/finalize, the interrupted work was
# abandoned and the outer safety net could duplicate or lose the outcome).
# Covers capturing logs, tearing the container down, and writing the
# terminal ModelUsage/HarnessJob state -- all Docker-control calls the
# wait loop's own timeout branch already runs before returning. Must cover
# the worst-case sum of every Docker-control call this path can make
# (Codex code-review P2, branch 2 round 7: the old default of 30s covered
# barely one of these calls, let alone all of them; round 8: the round-7
# fix's own math missed `_teardown_container()`'s `container_is_running()`
# inspect call between `stop_container` and `kill_container`, one more
# full `_DOCKER_CONTROL_TIMEOUT_SECONDS` the 150s draft didn't cover):
#   capture_logs      _DOCKER_CONTROL_TIMEOUT_SECONDS                 = 30
#   stop_container    HARNESS_KILL_GRACE_SECONDS + _DOCKER_CONTROL_...= 10 + 30 = 40
#   container_is_running (inspect, before kill)  _DOCKER_CONTROL_...  = 30
#   kill_container     _DOCKER_CONTROL_TIMEOUT_SECONDS (if running)   = 30
#   remove_container   _DOCKER_CONTROL_TIMEOUT_SECONDS                = 30
#                                                              total   = 160
# 180s leaves headroom above that worst case without drifting out of sync
# every time one of those timeouts changes.
HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS = env.int(
    "HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS", default=180
)

# Recovery scan (brief §2, brake #4): mirrors agents'/memory's/knowledge's/
# tools' identical recovery-grace convention. A job still `running` past
# HARNESS_MAX_JOB_SECONDS plus this buffer, never finalized by its own
# worker (crash/OOM/hard-kill), is swept -- the sweep uses the job's own
# `container_id` to run the same stop/kill/rm sequence, rather than
# assuming a dead worker means a dead container.
HARNESS_RECOVERY_GRACE_SECONDS = env.int("HARNESS_RECOVERY_GRACE_SECONDS", default=300)

# A second, distinct recovery case (Codex code-review P2, branch 2 round
# 2; mirrors agents' identical AGENT_DISPATCH_RECOVERY_GRACE_SECONDS): a
# HarnessJob still `pending` this long since creation had its
# transaction.on_commit-scheduled dispatch lost (e.g. the broker was down
# at commit time -- the exact swallowed-exception path in
# harness.services.dispatch_harness_job). Simply re-dispatched; the
# execution engine's own `_claim_job` fencing makes this safe even if the
# original dispatch actually does arrive later.
HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS = env.int(
    "HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS", default=60
)

# A third, distinct recovery case (Codex code-review P2, branch 2 round 7):
# a job that already finalized as `failed`/`cancelled`/`succeeded` can still
# carry a non-empty `container_id` if its own `_safe_remove_container` call
# genuinely failed (as opposed to "already gone") -- that container is then
# leaked on the host with nothing left to ever revisit it, since the first
# recovery case above only ever sweeps `status=running` jobs. Swept once a
# terminal job has sat this long since its last update, to avoid racing an
# in-flight finalize that is still legitimately retrying removal itself.
HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS = env.int(
    "HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS", default=300
)

# Per-agent container image (brief: "a single Jarvis-owned Dockerfile per
# supported agent, pinned to a specific, recorded base-image digest --
# never `latest`" -- see harness/docker/*.Dockerfile). Image *tags* are
# configured here rather than hardcoded so a reviewed digest bump
# (the brief's own "reviewed bump/rebuild cadence") is an env change, not
# a code change.
HARNESS_IMAGE_CLAUDE_CODE = env(
    "HARNESS_IMAGE_CLAUDE_CODE", default="jarvis-harness-claude-code:unbuilt"
)
HARNESS_IMAGE_CODEX_CLI = env("HARNESS_IMAGE_CODEX_CLI", default="jarvis-harness-codex-cli:unbuilt")

# Docker network implementing the container policy's egress allowlist
# (brief: "no general internet access... enforced via Docker network
# policy"). Provisioning the actual restricted-egress network is runner
# setup, not application code -- this only names which network a launched
# container joins.
HARNESS_DOCKER_NETWORK = env("HARNESS_DOCKER_NETWORK", default="jarvis-harness-egress")

# Resource limits applied to every launched container (brief: "a runaway or
# hostile process inside the container cannot exhaust the runner").
HARNESS_CONTAINER_MEMORY = env("HARNESS_CONTAINER_MEMORY", default="2g")
HARNESS_CONTAINER_CPUS = env("HARNESS_CONTAINER_CPUS", default="1.0")
HARNESS_CONTAINER_PIDS_LIMIT = env.int("HARNESS_CONTAINER_PIDS_LIMIT", default=256)

# --- Harness writable runner (Phase 3 branch 1, brief §boundary) ---

# The push/clean-clone credential for HarnessImplementationJob, resolved
# from env only -- never stored on HarnessRepo (Phase 2 branch 1's "no
# credentials field" rule stands; CLAUDE.md "secrets only via env"). A
# single SSH deploy key shared across every registered repo, mirroring
# harness.execution._resolve_cli_env's identical one-credential-per-
# capability simplicity -- a per-repo credential matrix is a real feature
# this phase doesn't need yet. Empty by default: a repo needing
# implementation/push (not just discovery) genuinely can't be used for
# that until this is configured -- fails closed, not a silent no-auth
# attempt.
HARNESS_GIT_SSH_KEY_PATH = env("HARNESS_GIT_SSH_KEY_PATH", default="")

# --- Harness implementation jobs (Phase 3 branch 2, brief §boundary) ---

# Self-modification hard-block (ADR-004: "agents can never modify their own
# definitions or permissions"): remote URLs recognized as "this Jarvis
# installation itself", compared via harness.repo_identity's canonical
# normalizer, not exact string match -- so the https/ssh/scp-like forms of
# the same remote are all correctly recognized. Empty by default -- and since
# Phase 3 branch 5 an empty list REFUSES every implementation job (fail
# closed): the guard cannot tell whether a repo is Jarvis itself until the
# owner configures this installation's own remote(s) here.
HARNESS_SELF_REPO_IDENTIFIERS = [
    value.strip()
    for value in env.list("HARNESS_SELF_REPO_IDENTIFIERS", default=[])
    if value.strip()
]

# How long a finished-but-undecided implementation worktree is kept on disk
# waiting for a Gate B (push approval) decision, across however long the
# owner takes and across a possible worker restart (brief §boundary,
# "worktree lifetime across the Gate-B wait"). The recovery scan (branch 4)
# sweeps and fails closed any worktree past this deadline whose job never
# reached a terminal state -- a backstop, not the primary cleanup path
# (every terminal transition cleans up its own worktree immediately).
HARNESS_WORKTREE_RETENTION_HOURS = env.int("HARNESS_WORKTREE_RETENTION_HOURS", default=72)

# --- Telegram bot (MVP 9 §4) ---

# Bot API token from @BotFather -- used only server-side by
# integrations/telegram_client.py to call the Bot API. Never the same
# value as TELEGRAM_WEBHOOK_SECRET below.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")

# A value WE choose and register with Telegram via `setWebhook`
# (`manage.py register_telegram_webhook`) -- Telegram echoes it back on
# every webhook call as the X-Telegram-Bot-Api-Secret-Token header, which
# the webhook view verifies before touching the database or enqueuing
# anything. Empty by default: an unconfigured secret must never match an
# empty/missing header (fail closed, not fail open).
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")

# Error tracking — active only when a DSN is configured.
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1, send_default_pii=False)

# TODO(MVP 1): structlog JSON logging configuration bound to request_id.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
