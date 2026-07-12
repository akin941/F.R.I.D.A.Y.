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
    "agents",
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
# (MVP 5 §1) — lets a single-user personal workspace's own owner approve
# their own request, since there is no agent runtime yet and (today) no
# second workspace member to be the decider. NEVER set this in a real
# deployment: `approvals.apps.ApprovalsConfig.ready()` refuses to boot the
# process at all if this is True while DEBUG is False.
DEV_ALLOW_SELF_APPROVAL = env.bool("DEV_ALLOW_SELF_APPROVAL", default=False)

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
