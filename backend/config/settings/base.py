"""Base settings — everything configurable comes from the environment (12-factor)."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import environ
from django.core.exceptions import ImproperlyConfigured

from core import legacy_env

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(DJANGO_DEBUG=(bool, False))
# Read .env from the repo root if present (local development convenience).
environ.Env.read_env(BASE_DIR.parent / ".env")


def _compose_secret(name):
    """One value from the Docker Compose stack's gitignored
    `deploy/docker/.env` (written by `make secrets`), or "" when absent.

    Only used to build the HOST-side development defaults for DATABASE_URL
    and REDIS_URL below, so `make test`, `make harness-runner` and host-side
    `manage.py` commands reach the compose Postgres/Redis (published on
    127.0.0.1) with the generated passwords without copying them anywhere.
    Deliberately reads single keys rather than loading the whole file into
    the process environment (it also holds provider keys and stack-only
    toggles). Inside a container the file does not exist -- compose passes
    the real URLs as env vars -- and production settings require them
    explicitly (config/settings/prod.py).
    """
    path = BASE_DIR.parent / "deploy" / "docker" / ".env"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    value = ""
    for line in lines:
        key, sep, raw = line.strip().partition("=")
        if sep and key == name:
            value = raw.strip().strip("'\"")
    return value


DEBUG = env("DJANGO_DEBUG")

# `or` fallbacks: a copied .env.example leaves vars set-but-empty; treat empty as unset.
# The hard-coded fallback is a development convenience only: without DEBUG
# (config/settings/dev.py turns it on) a missing key fails hard instead of
# silently signing sessions/tokens with a publicly known value.
DEV_FALLBACK_SECRET_KEY = "dev-insecure-key-change-me-0123456789abcdef"
SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DEBUG is off.")
    SECRET_KEY = DEV_FALLBACK_SECRET_KEY
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
    # Refresh-token blacklist (Phase 4 branch 1): rotation blacklists the
    # previous refresh token and POST /api/v1/auth/logout blacklists the
    # current one; `flushexpiredtokens` runs from beat (config/celery.py).
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    # Friday kernel
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
    # Phase 5 (ADR-010): outside-world bridges and deploys. Kernel apps,
    # registered empty in branch 1 so their import contracts bite first.
    "connectors",
    "deploys",
    # Phase 5 closeout: operations tooling (`manage.py preflight`). A leaf:
    # no app imports it (import-linter).
    "ops",
    # Phase 6 branch 2 (ADR-011 decision 6): the voice transport, a leaf app.
    "voice",
    # Phase 6 branch 5 (brief A8): the HUD's read-only event stream, a leaf.
    "hud",
    # Phase 9 (ADR-013, brief D2): the Friday Node server side. Only tools,
    # hud, ops and config may import it (import-linter).
    "nodes",
    # Phase 12 (ADR-016, brief D1-D5): owner schedules, proactive runs and
    # notices. Only assistant, hud and ops may import it (import-linter).
    "proactive",
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
# Default points at the Compose postgres (host port 5433) with the password
# `make secrets` generated into deploy/docker/.env -- never a committed one.
_compose_pg_password = _compose_secret("POSTGRES_PASSWORD")
# The Compose database keeps its pre-rename user/name `jarvis` (an existing
# data volume was initialized with them; see deploy/docker/compose.yml).
_DEFAULT_DATABASE_URL = (
    f"postgres://jarvis:{_compose_pg_password}@localhost:5433/jarvis"
    if _compose_pg_password
    else "postgres://jarvis@localhost:5433/jarvis"
)
DATABASES = {"default": env.db_url_config(env("DATABASE_URL", default="") or _DEFAULT_DATABASE_URL)}
# Parallel test runs (one per git worktree) against the same Postgres would
# otherwise all create/destroy the same `test_<name>` database.
_TEST_DATABASE_NAME = env("DJANGO_TEST_DATABASE_NAME", default="")
if _TEST_DATABASE_NAME:
    DATABASES["default"]["TEST"] = {"NAME": _TEST_DATABASE_NAME}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Celery. Host-side default: the compose Redis (host port 6380) with the
# `make secrets` password from deploy/docker/.env (Redis requires AUTH).
_compose_redis_password = _compose_secret("REDIS_PASSWORD")
_DEFAULT_REDIS_URL = (
    f"redis://:{_compose_redis_password}@localhost:6380/0"
    if _compose_redis_password
    else "redis://localhost:6380/0"
)
REDIS_URL = env("REDIS_URL", default="") or _DEFAULT_REDIS_URL
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
# Deploy runner queue (Phase 5 branch 6, brief D10): every `deploys.tasks.*`
# task -- the SSH deploy run, the target `status` refresh and the recovery
# scan -- goes to its own queue, consumed by the same runner process as the
# harness queue (`make harness-runner`, `-Q harness,deploy`), so the deploy
# SSH key lives only on the runner host, like HARNESS_GIT_SSH_KEY_PATH.
# With no runner up, deploys wait `pending`.
DEPLOY_CELERY_QUEUE = env("DEPLOY_CELERY_QUEUE", default="deploy")
CELERY_TASK_ROUTES = {
    "harness.tasks.*": {"queue": HARNESS_CELERY_QUEUE},
    "deploys.tasks.*": {"queue": DEPLOY_CELERY_QUEUE},
}


def redis_cache_location(redis_url):
    """REDIS_CACHE_URL's default: the broker's Redis, database 1, so cache
    keys never mix with Celery's queues in database 0."""
    parts = urlsplit(redis_url)
    return urlunsplit((parts.scheme, parts.netloc, "/1", parts.query, parts.fragment))


# Cache (Phase 4 branch 1). DRF throttles and the Telegram relay limiter
# (integrations/tasks.py) count in the default cache; with per-process
# LocMem, web and worker (and every gunicorn worker) each counted
# separately. Django's built-in RedisCache (no extra dependency) makes the
# counters shared. Used only when REDIS_CACHE_URL or REDIS_URL is actually
# set in the environment (compose web/worker, production); a bare host
# process without them -- CI's e2e runserver, an ad-hoc shell -- keeps
# LocMem. The test settings (config/settings/test.py) always use LocMem.
_REDIS_CACHE_URL = env("REDIS_CACHE_URL", default="") or (
    redis_cache_location(REDIS_URL) if env("REDIS_URL", default="") else ""
)
if _REDIS_CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _REDIS_CACHE_URL,
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- API / auth ---

REST_FRAMEWORK = {
    # The web session of a trusted device -- the only authenticator that
    # yields CHANNEL_WEB (Phase 4 branch 2). PAT auth is allowlisted per view.
    "DEFAULT_AUTHENTICATION_CLASSES": ("accounts.authentication.DeviceJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # Phase 4 branch 3: every error body carries a stable `code`
    # (core/api_errors.py, vocabulary in core/error_codes.py) -- the handler
    # for raised exceptions, the renderer as the backstop for error
    # responses views build by hand.
    "EXCEPTION_HANDLER": "core.api_errors.exception_handler",
    "DEFAULT_RENDERER_CLASSES": (
        "core.api_errors.CodedJSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ),
    # Scoped rates (ScopedRateThrottle, counted in the default cache):
    # - "chat": provider-cost message send (assistant/views.py).
    # - "refresh": the anonymous session endpoints (refresh, logout, device
    #   session mint; accounts/urls.py). Django only ever sees the BFF's
    #   address as REMOTE_ADDR, so these are effectively GLOBAL rates, not
    #   per client -- correct for a single-owner system (ADR-008): they bound
    #   refresh-token replay for the whole install. `/auth/enroll` has its
    #   own two-tier throttle (ENROLL_RATE_* below).
    # - "cost": the remaining cost-bearing creates -- agent creation
    #   (synchronous planner LLM call), harness discovery/implementation
    #   jobs (each up to HARNESS_MAX_JOB_COST) and knowledge documents
    #   (embeddings). One shared per-user budget across all of them.
    # - "voice_stt" / "voice_tts" (Phase 6 D9): the two voice endpoints,
    #   per user; TTS is per sentence, hence the higher rate.
    "DEFAULT_THROTTLE_RATES": {
        "chat": "30/min",
        "refresh": "30/min",
        "cost": "60/hour",
        "voice_stt": env("VOICE_STT_RATE", default="20/min"),
        "voice_tts": env("VOICE_TTS_RATE", default="120/min"),
        # Phase 6 branch 3: the "bu ne kadar tuttu?" receipt read.
        "voice_receipt": env("VOICE_RECEIPT_RATE", default="30/min"),
        # Phase 6 branch 5: HUD event-stream (re)connects; one stream lives
        # at most HUD_STREAM_MAX_SECONDS, so ~1/min per open HUD tab.
        "hud_events": env("HUD_EVENTS_RATE", default="20/min"),
        # Phase 10 branch 5: terminal-panel output streams (one per
        # execution opened in the panel, reconnecting at most once a minute).
        "hud_terminal": env("HUD_TERMINAL_RATE", default="30/min"),
        # Phase 8A branch 3: Claude Code hook-event batches from
        # `friday-hook`, per hook token (one POST per hook invocation).
        "hook_ingest": env("HOOK_INGEST_RATE", default="240/min"),
        # Phase 9 branch 1: node credential rotation, per node owner (a node
        # rotates every NODE_CREDENTIAL_ROTATE_AFTER_DAYS; this only bounds
        # a stolen secret hammering the endpoint).
        "node_rotate": env("NODE_ROTATE_RATE", default="10/hour"),
        # Phase 9 branch 2 (D4): anonymous node pairing (`POST
        # /api/v1/nodes/pair`). Like "refresh" this is effectively global
        # (one owner, one or two Macs); it bounds code guessing on top of
        # the per-code PIN attempt burn. Fails closed when Redis is down.
        "node_pair": env("NODE_PAIR_RATE", default="10/hour"),
        # Phase 11 D3: node voice credential mints, per node owner (the host
        # mints one per child start and <= 60 s before each expiry).
        "node_voice_mint": env("NODE_VOICE_MINT_RATE", default="30/hour"),
        # Phase 11 D5: live-session opens, per owner, so a misbehaving voice
        # child cannot mint empty conversations in a loop.
        "node_voice_session": env("NODE_VOICE_SESSION_RATE", default="60/hour"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    # A rotated-out refresh token is blacklisted, so a stolen-and-replayed
    # older token stops working after the legitimate client's next refresh.
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

# Django's four stock validators; 12 characters minimum. Enforced wherever
# a password is set through a human-facing path (`bootstrap_owner`). The
# owner's password is only a Django-admin break-glass credential: no API
# endpoint accepts it (Phase 4 branch 2).
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# CLI personal access tokens (MVP 9 §1) expire (Phase 4 branch 1): the mint
# request may choose a lifetime in days, capped at PAT_MAX_TTL_DAYS;
# omitted, it gets PAT_DEFAULT_TTL_DAYS.
PAT_DEFAULT_TTL_DAYS = env.int("PAT_DEFAULT_TTL_DAYS", default=90)
PAT_MAX_TTL_DAYS = env.int("PAT_MAX_TTL_DAYS", default=365)

# --- Device trust (Phase 4 branch 2, ADR-008 decision 2) ---
#
# There is no signup and no password login: the owner enrolls devices at
# /enroll with a pairing code (`manage.py issue_pairing_code`, or "Yeni cihaz
# eşleştir" on a trusted session) or the remote access key, and each trusted
# device mints its own JWT sessions (accounts/devices.py).

# A device unused for this long can no longer authenticate or mint a
# session (re-enroll it).
DEVICE_IDLE_EXPIRY_DAYS = env.int("DEVICE_IDLE_EXPIRY_DAYS", default=90)
# Friday Node credentials (Phase 9 spike S9.0, ADR-013 decision 2): a
# `kind=node` TrustedDevice holds a `jnd_` secret in the Mac's Keychain and
# never mints a JWT. No browser idle expiry; instead:
# - a hard age cap counted from the last rotation (refused afterwards);
NODE_CREDENTIAL_MAX_AGE_DAYS = env.int("NODE_CREDENTIAL_MAX_AGE_DAYS", default=90)
# - the rotation age the server advertises to the node (`hello.ack`);
NODE_CREDENTIAL_ROTATE_AFTER_DAYS = env.int("NODE_CREDENTIAL_ROTATE_AFTER_DAYS", default=30)
# - a node with no successful connection for this long is expired;
NODE_IDLE_EXPIRY_DAYS = env.int("NODE_IDLE_EXPIRY_DAYS", default=30)
# - after a rotation the previous secret stays valid this long, or until the
#   new secret is first used, whichever comes first.
NODE_ROTATION_GRACE_SECONDS = env.int("NODE_ROTATION_GRACE_SECONDS", default=600)

# Friday Node protocol signing (Phase 9 brief D4). NODE_SIGNING_KEY is an
# Ed25519 private key, base64 of its raw 32 bytes, from the environment only
# (`deploy/docker/gen-secrets.sh` writes one); its key id is derived (first
# 16 hex of sha256 of the public key). NODE_SIGNING_KEYS_RETIRED lists
# base64 raw PUBLIC keys that still verify but never sign (rollover is
# re-pairing in Phase 9). A missing or malformed key refuses node pairing
# and invokes (and fails preflight); nothing else is affected.
NODE_SIGNING_KEY = env("NODE_SIGNING_KEY", default="")
NODE_SIGNING_KEYS_RETIRED = env.list("NODE_SIGNING_KEYS_RETIRED", default=[])
# A read `invoke` envelope's lifetime (expires_at - issued_at), at most the
# protocol's 30 s ceiling (nodes.protocol.messages).
NODE_READ_INVOKE_TTL_SECONDS = env.int("NODE_READ_INVOKE_TTL_SECONDS", default=30)
# The largest canonical `result.output` a node may return (64 KiB, D5).
NODE_MAX_RESULT_BYTES = env.int("NODE_MAX_RESULT_BYTES", default=64 * 1024)
# Phase 9 branch 4 (brief D6): the invoke bus. A read waits at most
# NODE_INVOKE_TIMEOUT_SECONDS for its result (below the envelope TTL); a
# result nobody waits for expires from Redis after NODE_RESULT_TTL_SECONDS;
# at most NODE_MAX_INFLIGHT reads per node run at once, counted in Redis
# across web, worker and the gateway (beyond it: `busy`).
NODE_INVOKE_TIMEOUT_SECONDS = env.int("NODE_INVOKE_TIMEOUT_SECONDS", default=10)
NODE_RESULT_TTL_SECONDS = env.int("NODE_RESULT_TTL_SECONDS", default=30)
NODE_MAX_INFLIGHT = env.int("NODE_MAX_INFLIGHT", default=4)
# Phase 10 branch 3 (brief D2, D6, D7). A gate-class grant lives
# NODE_GRANT_TTL_SECONDS (delivery plus the node's 60 s confirmation window;
# refused at boot above the 120 s protocol ceiling). An approved node action
# not dispatched within NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS of its
# decision fails `stale_dispatch` (nothing queued to run later). The
# server-scrubbed output tail is cleared after NODE_ACTION_TAIL_RETENTION_DAYS.
NODE_GRANT_TTL_SECONDS = env.int("NODE_GRANT_TTL_SECONDS", default=90)
NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS = env.int(
    "NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS", default=60
)
NODE_ACTION_TAIL_RETENTION_DAYS = env.int("NODE_ACTION_TAIL_RETENTION_DAYS", default=14)
# PIN pairing codes are accepted only from these networks (LAN gating):
# loopback, RFC 1918, IPv6 ULA and the CGNAT range Tailscale uses. Strong
# codes and the remote key work from any network. Defense in depth, not the
# boundary: codes are issuable only by a trusted principal, single-use,
# short-lived, attempt-burned and hashed.
DEVICE_PAIRING_NETWORKS = [
    net.strip()
    for net in env.list(
        "DEVICE_PAIRING_NETWORKS",
        default=[
            "127.0.0.0/8",
            "::1/128",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "fc00::/7",
            "100.64.0.0/10",
        ],
    )
    if net.strip()
]
# Django only ever sees the BFF's address. The BFF forwards the browser's IP
# (taken ONLY from its TRUSTED_CLIENT_IP_HEADER, set by the owner's reverse
# proxy) as X-Friday-Client-IP together with X-Friday-BFF-Key; Django
# ignores the IP header unless the key matches this shared secret. Empty =
# no client IP is ever trusted.
# Falls back to the deprecated pre-rename JARVIS_BFF_SHARED_SECRET.
FRIDAY_BFF_SHARED_SECRET = legacy_env.getenv("FRIDAY_BFF_SHARED_SECRET")
# Without a validated client IP (no trusted proxy, e.g. bare `next start`),
# a PIN is accepted only when this is on ("LAN gating off"). Dev default
# true (config/settings/dev.py), production default false: pair with a
# strong code or the remote key instead.
DEVICE_PAIRING_ALLOW_UNKNOWN_IP = env.bool("DEVICE_PAIRING_ALLOW_UNKNOWN_IP", default=False)
# Django admin is the owner password's only use (break-glass, Phase 4 Q3):
# `admin/` is routed only when this is on. Dev default true
# (config/settings/dev.py), production default false.
DJANGO_ADMIN_ENABLED = env.bool("DJANGO_ADMIN_ENABLED", default=False)
# Two-tier enrollment throttle (accounts/throttling.py): a tight limit per
# validated client IP plus a much looser install-wide ceiling, so a flood
# from one source cannot exhaust the bucket the owner needs. Without a
# validated client IP only the global ceiling applies (accepted, documented
# residual risk; the host-shell `issue_pairing_code` path is never
# throttled). Format "<count>/<n><s|m|h|d>", e.g. "5/10m".
ENROLL_RATE_PER_SOURCE = env("ENROLL_RATE_PER_SOURCE", default="5/10m")
ENROLL_RATE_GLOBAL = env("ENROLL_RATE_GLOBAL", default="60/h")

# --- OpenAI-compatible facade (Phase 4 branch 6, ADR-008 decision 6) ---
# `/v1/chat/completions` + `/v1/models` for Open WebUI-class clients, with a
# PAT of kind `openai`: chat only, low trust. Per token: FACADE_RATE requests
# ("<count>/<n><s|m|h|d>"), at most FACADE_MAX_CONCURRENT_STREAMS completions
# in flight (a slot expires after FACADE_STREAM_SLOT_TTL_SECONDS if a worker
# dies mid-stream), and a new user turn (or, for `friday-lite`, the whole
# message list) of at most FACADE_MAX_REQUEST_CHARS characters.
FACADE_RATE = env("FACADE_RATE", default="20/min")
FACADE_MAX_CONCURRENT_STREAMS = env.int("FACADE_MAX_CONCURRENT_STREAMS", default=2)
FACADE_STREAM_SLOT_TTL_SECONDS = env.int("FACADE_STREAM_SLOT_TTL_SECONDS", default=300)
FACADE_MAX_REQUEST_CHARS = env.int("FACADE_MAX_REQUEST_CHARS", default=32000)

# --- Trash (ADR-009) ---
# Soft-deleted knowledge documents and tasks are purged permanently this
# many days after deletion (daily beat task); an explicit earlier purge is
# the gate-class `purge_trash` tool.
TRASH_RETENTION_DAYS = env.int("TRASH_RETENTION_DAYS", default=30)

# --- Continuous-learning ingestion (Phase 8A, ADR-012) ---
# Raw episodes expire this many days after ingestion (owner decision d10):
# 30 in a personal workspace, 14 in a company workspace; a daily beat
# hard-deletes expired rows. Each source gets a daily token budget for
# extraction (D13; the gateway's purpose budgets are the hard stop).
# MEMORY_OWNER_GIT_EMAILS: comma-separated author e-mails whose commits
# (without an agent trailer) count as owner-authored text (D5, branch 4).
MEMORY_EPISODE_TTL_DAYS_PERSONAL = env.int("MEMORY_EPISODE_TTL_DAYS_PERSONAL", default=30)
MEMORY_EPISODE_TTL_DAYS_FIRM = env.int("MEMORY_EPISODE_TTL_DAYS_FIRM", default=14)
MEMORY_INGESTION_DEFAULT_DAILY_TOKEN_BUDGET = env.int(
    "MEMORY_INGESTION_DEFAULT_DAILY_TOKEN_BUDGET", default=40000
)
MEMORY_OWNER_GIT_EMAILS = [
    e.strip().lower() for e in env.list("MEMORY_OWNER_GIT_EMAILS", default=[]) if e.strip()
]
# Claude Code hook relay (Phase 8A branch 3, D6/D12): a coding session with
# no hook event for this many hours shows as `stale` in the sessions API.
# The ingest endpoint's per-token rate is HOOK_INGEST_RATE (throttle scope
# `hook_ingest`, REST_FRAMEWORK above).
MEMORY_CODING_SESSION_STALE_HOURS = env.int("MEMORY_CODING_SESSION_STALE_HOURS", default=24)
# Branch 4 (D5): the git producer's first run (no cursor yet) looks back
# this many days; later runs start an hour before the cursor's committer
# date. The calendar producer reads yesterday and today.
MEMORY_GIT_INGEST_LOOKBACK_DAYS = env.int("MEMORY_GIT_INGEST_LOOKBACK_DAYS", default=2)
# Branch 5 (D10): the morning card lists pending learned proposals created
# in the last this-many days (the caps themselves are code constants in
# memory.ingestion.consolidation, not settings).
MEMORY_LEARNING_DIGEST_DAYS = env.int("MEMORY_LEARNING_DIGEST_DAYS", default=2)
# Phase 8B branch 8 (D13): the transcript pull's beat interval (worker beat,
# `tools.tasks.pull_transcripts_task`; config/celery.py reads the same env).
MEMORY_TRANSCRIPT_PULL_MINUTES = env.int("MEMORY_TRANSCRIPT_PULL_MINUTES", default=15)

# --- Hybrid retrieval (Phase 8B branch 4, brief D8) ---
# `legacy` (default): `memory.context.select_context_memories`, unchanged.
# `hybrid`: full text + vector lists fused with RRF, recency decay and graph
# hops (`memory.retrieval.hybrid`), with a `<memory_fact>` block. The
# closeout flips the default only if the eval set shows hybrid >= legacy.
MEMORY_RETRIEVAL_MODE = env("MEMORY_RETRIEVAL_MODE", default="legacy")
if MEMORY_RETRIEVAL_MODE not in ("legacy", "hybrid"):
    raise ImproperlyConfigured("MEMORY_RETRIEVAL_MODE must be 'legacy' or 'hybrid'.")
# Recency half-life for episodic/work_log memories and learned facts.
MEMORY_RETRIEVAL_HALF_LIFE_DAYS = env.int("MEMORY_RETRIEVAL_HALF_LIFE_DAYS", default=45)
# Graph hops from the top facts (1 or 2; 0 disables the expansion).
MEMORY_RETRIEVAL_HOPS = env.int("MEMORY_RETRIEVAL_HOPS", default=1)
if not 0 <= MEMORY_RETRIEVAL_HOPS <= 2:
    raise ImproperlyConfigured("MEMORY_RETRIEVAL_HOPS must be 0, 1 or 2.")

# --- Memory eval set (Phase 8B branch 3, brief D9) ---
# `memory_eval run|baseline|compare` run only under the eval settings module
# (`config.settings.eval`, its own database), which flips this to True; the
# live settings keep it False, and `memory_eval record` refuses True.
MEMORY_EVAL_MODE = False
# The owner's private, never-committed case set (mode 0700, outside the
# repo). Its results go to `<dir>/results/`.
MEMORY_EVAL_PRIVATE_DIR = env("MEMORY_EVAL_PRIVATE_DIR", default="~/friday-evals")
# Where public-set result files go (CI points it at a temporary directory).
# Kept outside the private directory, which may hold only case files and
# `results/` (`check_private_dir` refuses anything else).
MEMORY_EVAL_RESULTS_DIR = env("MEMORY_EVAL_RESULTS_DIR", default="~/friday-evals-public-results")

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
# Tier routing map (phase 0 skeleton, MASTER_PLAN §J; full map Phase 5 D11,
# docs/cost/token-optimization.md layer 4): purpose -> tier name
# ("cheap"/"mid"/"strong", ai_core.ModelTier). A purpose's tier only
# REORDERS eligible providers (tier members first, then priority); with no
# ModelTier rows for that tier the gateway keeps plain priority order, so
# the map is inert until `seed_ai_defaults` (or the admin) adds tier rows.
# Deliberately a settings dict, not env/DB, until it needs runtime editing
# (phase-0 brief §4). Every `purpose=` literal in production code must be
# either mapped here or listed in AI_UNTIERED_PURPOSES below -- a CI test
# (ai_core/tests/test_purpose_coverage.py) fails otherwise.
AI_PURPOSE_TIERS = {
    # cheap: classification, argument extraction, short structured summaries
    "router.intent": "cheap",
    "router.task_draft": "cheap",
    "router.connector_args": "cheap",  # Phase 5 branch 4 (D4)
    # Phase 10 D13: the node action argument step (untrusted, re-validated).
    "router.node_action_args": "cheap",
    # Phase 10 D9: the node action trust check (the `control` budget).
    "control.trust_check": "cheap",
    # Phase 12 D6/D18: the morning digest's one spoken summary per run
    # (personal data, local provider, the `proactive` budget).
    "proactive.digest": "cheap",
    # Phase 12 D15/D18: the per-turn instruction-skill matcher (the `skills`
    # budget; a timeout or refusal means "no skill").
    "skills.match": "cheap",
    "agent.skill_match": "cheap",
    "harness.briefing": "cheap",
    "harness.diff_summary": "cheap",
    "harness.review_summary": "cheap",
    "facade.ephemeral": "cheap",
    # mid: conversation, extraction, retrieval-grounded steps
    "chat": "mid",
    # Phase 12 D11/D18: the one completion of an async read's follow-up
    # (fenced result, no tools; personal per the read tool's data class).
    "chat.followup": "mid",
    "memory.extraction": "mid",
    "agent.read_step": "mid",
    # Phase 8A learning loop (brief D9/D10): owner-text episode extraction is
    # a short structured pass; consolidation dedupes, merges and ranks.
    "memory.episode_extraction": "cheap",
    "memory.consolidation": "mid",
    # Phase 8B learning (brief D4/D5/D15): the supersede judgement needs the
    # current facts in view; the weekly reflection finds patterns.
    "memory.fact_reconciliation": "mid",
    "memory.reflection": "mid",
    # Phase 8B branch 3 (D9, D15): eval-set answer generation (eval runs only).
    "memory.eval_answer": "mid",
    # strong: planning
    "agent.plan": "strong",
}
# The brief's `UNTIERED_PURPOSES` (D11): purposes that deliberately have no
# tier. Embeddings are pinned by their stored vector dimension (a tier swap
# would silently orphan every stored vector), and external-CLI usage rows
# (`record_external_usage`) have no provider for the gateway to route.
AI_UNTIERED_PURPOSES = frozenset(
    {
        # embeddings
        "memory.retrieval",
        "knowledge.embedding",
        "knowledge.retrieval",
        # external CLI usage (harness containers, ADR-008 decision 1)
        "harness.discovery",
        "harness.implementation",
        "harness.review",
        # speech (Phase 6 D5): capability, not tier, selects audio providers
        "voice.stt",
        "voice.tts",
        # `manage.py preflight` (Phase 5 closeout): pinned to one provider at
        # a time (`only_provider`), so a tier would never apply.
        "ops.preflight",
    }
)

# --- Gateway budgets (Phase 6 A3, ADR-003 amendment 2026-11-20) ---
# Seed values for the `audio` ai_core.AIBudget row (`seed_ai_defaults`
# creates it once; afterwards the owner edits the row in the admin, and a
# reseed never overwrites it). The budget covers AI_AUDIO_PURPOSES; local
# speech providers cost 0 and never trip it. Empty = no cap for that window.
AI_AUDIO_PURPOSES = ("voice.stt", "voice.tts")
AI_BUDGET_AUDIO_MONTHLY_USD = env("AI_BUDGET_AUDIO_MONTHLY_USD", default="20")
AI_BUDGET_AUDIO_SESSION_USD = env("AI_BUDGET_AUDIO_SESSION_USD", default="1")
AI_BUDGET_AUDIO_DAILY_USD = env("AI_BUDGET_AUDIO_DAILY_USD", default="")
# Phase 8A D13: the learning loop's gateway caps. `learning` is the shared
# monthly cap over both learning purposes (20 USD, ADR-012); each purpose
# also gets its own daily cap. Seeded once by `seed_ai_defaults`
# (`learning`, `learning_extraction_daily`, `learning_consolidation_daily`
# AIBudget rows), owner-editable afterwards. Empty = no cap.
# Phase 8B D15 adds fact reconciliation and the weekly reflection to the
# shared monthly cap, each with its own cap too (`learning_reconciliation_daily`,
# `learning_reflection_weekly`). The reflection runs at most once per
# workspace per ISO week, on Sunday, so its "weekly" cap is enforced as a
# daily window on that one day (AIBudget has no weekly window).
AI_LEARNING_PURPOSES = (
    "memory.episode_extraction",
    "memory.consolidation",
    "memory.fact_reconciliation",
    "memory.reflection",
)
AI_BUDGET_LEARNING_MONTHLY_USD = env("AI_BUDGET_LEARNING_MONTHLY_USD", default="20")
AI_BUDGET_EPISODE_EXTRACTION_DAILY_USD = env("AI_BUDGET_EPISODE_EXTRACTION_DAILY_USD", default="1")
AI_BUDGET_CONSOLIDATION_DAILY_USD = env("AI_BUDGET_CONSOLIDATION_DAILY_USD", default="0.5")
AI_BUDGET_RECONCILIATION_DAILY_USD = env("AI_BUDGET_RECONCILIATION_DAILY_USD", default="0.5")
AI_BUDGET_REFLECTION_WEEKLY_USD = env("AI_BUDGET_REFLECTION_WEEKLY_USD", default="1")
# Phase 8B D9/D15: the `learning_eval` monthly cap over the eval-set answer
# calls, seeded once by `seed_ai_defaults` (owner-editable afterwards). Not in
# AI_LEARNING_PURPOSES: eval spend never eats the nightly learning cap. The
# eval settings module may widen the purposes (the D11 compression compare).
AI_LEARNING_EVAL_PURPOSES = ("memory.eval_answer",)
AI_BUDGET_LEARNING_EVAL_MONTHLY_USD = env("AI_BUDGET_LEARNING_EVAL_MONTHLY_USD", default="5")
# Phase 10 D9: the `control` monthly cap over the node action trust check,
# created once by the ai_core data migration 0008 (and `seed_ai_defaults`),
# owner-editable afterwards. A refused call degrades the verdict to
# `warn:trust_check_unavailable`; it never blocks or approves anything.
AI_CONTROL_PURPOSES = ("control.trust_check",)
AI_BUDGET_CONTROL_MONTHLY_USD = env("AI_BUDGET_CONTROL_MONTHLY_USD", default="5")
# Phase 12 D18: the `proactive` cap over the morning digest's composition
# (3 USD/month, 0.25 USD/day), created once by the ai_core data migration
# 0009 (and `seed_ai_defaults`), owner-editable afterwards. A refused call
# degrades the digest to its template text; the run still succeeds.
AI_PROACTIVE_PURPOSES = ("proactive.digest",)
AI_BUDGET_PROACTIVE_MONTHLY_USD = env("AI_BUDGET_PROACTIVE_MONTHLY_USD", default="3")
AI_BUDGET_PROACTIVE_DAILY_USD = env("AI_BUDGET_PROACTIVE_DAILY_USD", default="0.25")
# Phase 12 D18: the `skills` cap over the instruction-skill purposes (5 USD
# a month), created once by the ai_core data migration 0010 (and
# `seed_ai_defaults`), owner-editable afterwards. Branches 6/7 add the
# curator and distiller purposes to the row. A refused match means "no
# skill"; the turn is answered without one.
AI_SKILLS_PURPOSES = ("skills.match",)
AI_BUDGET_SKILLS_MONTHLY_USD = env("AI_BUDGET_SKILLS_MONTHLY_USD", default="5")

# --- Pre-send compression stage (Phase 8B branch 6, brief D11) ---
# `ai_core/compression.py`: shortens long prior assistant replies through
# the `compressor` sidecar (Headroom, deploy/compressor/, internal network,
# no egress). Off by default, per purpose, with a hard code-level bypass set
# (`COMPRESSION_BYPASS`) that wins over this list. A purpose is added here
# only with a recorded `memory_eval compare --compression <purpose>` result
# that is not `inconclusive`, <= 1 point accuracy loss and zero extra leaks
# (the brief's go/no-go table). `chat` is the only candidate today.
AI_COMPRESSION_ENABLED = env.bool("AI_COMPRESSION_ENABLED", default=False)
AI_COMPRESSION_PURPOSES = frozenset(env.list("AI_COMPRESSION_PURPOSES", default=[]))
AI_COMPRESSION_URL = env("AI_COMPRESSION_URL", default="http://compressor:8080/compress")
# On a timeout or error the original messages are sent (fail open).
AI_COMPRESSION_TIMEOUT_MS = env.int("AI_COMPRESSION_TIMEOUT_MS", default=300)

# --- Voice / speech gateway (Phase 6 branch 1, ADR-011) ---
# Utterance cap (D2/D3): 30 s of 16 kHz mono PCM16 = 960 KB, below Django's
# 2.5 MB in-memory upload threshold, so audio never touches disk.
VOICE_MAX_UTTERANCE_SECONDS = env.int("VOICE_MAX_UTTERANCE_SECONDS", default=30)
# TTS caps (D3/D7): text per /voice/speech request and the synthesized
# audio's maximum duration (longer output is rejected, never passed on).
VOICE_TTS_MAX_CHARS = env.int("VOICE_TTS_MAX_CHARS", default=400)
VOICE_TTS_MAX_SECONDS = env.int("VOICE_TTS_MAX_SECONDS", default=60)
# STT language hint passed as `params["language"]` by the voice app (Q7);
# "" = provider auto-detect.
VOICE_STT_LANGUAGE = env("VOICE_STT_LANGUAGE", default="tr")
# Default TTS voice for a provider with no route/provider-specific voice.
VOICE_TTS_VOICE = env("VOICE_TTS_VOICE", default="tr_TR-dfki-medium")
# A2: language -> provider row name + voice (+ optional response_format /
# pcm_sample_rate). The routed provider is used only if it is eligible
# (active, keyed, personal_data_allowed); otherwise the gateway falls back
# to priority order among eligible rows. Switching `tr` to OpenRouter after
# the branch-3a listening test is VOICE_TTS_ROUTE_TR_PROVIDER=openrouter-tts
# plus the owner's d2 toggle on that row.
VOICE_TTS_ROUTES = {
    "tr": {
        "provider": env("VOICE_TTS_ROUTE_TR_PROVIDER", default="voice-tts-local"),
        "voice": env("VOICE_TTS_ROUTE_TR_VOICE", default="tr_TR-dfki-medium"),
    },
    "en": {
        "provider": env("VOICE_TTS_ROUTE_EN_PROVIDER", default="voice-tts-local"),
        "voice": env("VOICE_TTS_ROUTE_EN_VOICE", default="en_US-lessac-medium"),
    },
}
# Per-provider TTS defaults (voice id, wire format). OpenRouter's
# /audio/speech returns `pcm` or `mp3` only (spike A1); the adapter wraps
# PCM into WAV.
VOICE_TTS_PROVIDER_PARAMS = {
    "openrouter-tts": {
        "voice": env("OPENROUTER_TTS_VOICE", default="alloy"),
        "response_format": "pcm",
    },
}
# A5: `voice.VoiceTurnTrace` rows (stage timings only) are pruned daily
# after this many days (config/celery.py).
VOICE_TRACE_RETENTION_DAYS = env.int("VOICE_TRACE_RETENTION_DAYS", default=30)
# --- Live voice on the node (Phase 11 D3/D5) ---
# Hard lifetime of a `node_voice` token (minted by the node secret, handed
# to the voice child). Refused at boot above one hour: a leaked child
# credential must stay short-lived.
VOICE_LIVE_TOKEN_TTL_SECONDS = env.int("VOICE_LIVE_TOKEN_TTL_SECONDS", default=900)
if not 60 <= VOICE_LIVE_TOKEN_TTL_SECONDS <= 3600:
    raise ImproperlyConfigured("VOICE_LIVE_TOKEN_TTL_SECONDS must be between 60 and 3600.")
# A newer mint shortens the device's previous token to now + this grace
# (instead of revoking it), so a request in flight during rotation succeeds.
VOICE_LIVE_ROTATION_GRACE_SECONDS = env.int("VOICE_LIVE_ROTATION_GRACE_SECONDS", default=30)
if not 0 <= VOICE_LIVE_ROTATION_GRACE_SECONDS <= 300:
    raise ImproperlyConfigured("VOICE_LIVE_ROTATION_GRACE_SECONDS must be between 0 and 300.")
# `POST /api/v1/voice/live/sessions` reuses the latest live conversation
# whose last activity is younger than this; otherwise it opens a new one.
VOICE_LIVE_CONVERSATION_REUSE_MINUTES = env.int("VOICE_LIVE_CONVERSATION_REUSE_MINUTES", default=30)
# Phase 11 D11 (roadmap d3): always-on wake mode on the node is permitted
# only while this is on AND, for the wake model the child loaded, the latest
# `household` evaluation (>= 24 h) has a FAR per hour <= the budget and the
# latest `positives` evaluation an FRR <= the budget, both measured at the
# threshold and refractory period the child runs at. The server can only
# say no: it never turns always-on on when the Mac chose push-to-talk.
VOICE_LIVE_ALWAYS_ON_ENABLED = env.bool("VOICE_LIVE_ALWAYS_ON_ENABLED", default=False)
VOICE_LIVE_MAX_FAR_PER_HOUR = env("VOICE_LIVE_MAX_FAR_PER_HOUR", default="0.2")
VOICE_LIVE_MAX_FRR = env("VOICE_LIVE_MAX_FRR", default="0.10")
try:
    _far_budget = Decimal(VOICE_LIVE_MAX_FAR_PER_HOUR)
    _frr_budget = Decimal(VOICE_LIVE_MAX_FRR)
except InvalidOperation as _exc:
    raise ImproperlyConfigured(
        "VOICE_LIVE_MAX_FAR_PER_HOUR / VOICE_LIVE_MAX_FRR: decimals."
    ) from _exc
if not (_far_budget.is_finite() and _far_budget >= 0 and _frr_budget.is_finite()):
    raise ImproperlyConfigured("VOICE_LIVE_MAX_FAR_PER_HOUR / VOICE_LIVE_MAX_FRR: finite, >= 0.")
if not 0 <= _frr_budget <= 1:
    raise ImproperlyConfigured("VOICE_LIVE_MAX_FRR must be between 0 and 1.")
# Phase 11 D12: the storm backstop -- more transcriptions than this per node
# device in a sliding 60 s window engage `accounts.VoiceLiveMute` (423 on
# every node voice route until the owner releases it on screen).
VOICE_LIVE_STORM_MAX_STT_PER_MINUTE = env.int("VOICE_LIVE_STORM_MAX_STT_PER_MINUTE", default=8)
if VOICE_LIVE_STORM_MAX_STT_PER_MINUTE < 1:
    raise ImproperlyConfigured("VOICE_LIVE_STORM_MAX_STT_PER_MINUTE must be at least 1.")
# The voice upload cap (VOICE_MAX_UTTERANCE_SECONDS, 960 KB at 30 s) must
# stay below FILE_UPLOAD_MAX_MEMORY_SIZE (Django default 2.5 MB, unset
# here): the transcription view installs only the in-memory upload handler,
# so audio never touches disk (D2).

# --- Preflight (Phase 5 closeout, `manage.py preflight`) ---
# Live checks of the running stack. Each URL left empty makes its check a
# warning ("not configured"), never a failure.
# Backend liveness URL, e.g. http://localhost:8000/healthz inside `web`.
PREFLIGHT_WEB_URL = env("PREFLIGHT_WEB_URL", default="")
# The Next.js BFF's base URL (served page, its scripts, its build id).
PREFLIGHT_BFF_URL = env("PREFLIGHT_BFF_URL", default="")
# The built frontend's `.next/BUILD_ID`, compared with the served build.
PREFLIGHT_FRONTEND_BUILD_ID_FILE = env("PREFLIGHT_FRONTEND_BUILD_ID_FILE", default="")
PREFLIGHT_TIMEOUT_SECONDS = env.int("PREFLIGHT_TIMEOUT_SECONDS", default=10)
# Phase 6 closeout (brief A7): "a `?mute=1` voice tab is silent" is browser
# behaviour, so the `voice_mute` check runs a real browser -- normally
#   npm --prefix ../frontend run test:e2e -- e2e/voice-mute.e2e.ts
# against the running BFF (split with shlex, run without a shell, output
# dropped). Empty = the check is a warning.
PREFLIGHT_VOICE_MUTE_CMD = env("PREFLIGHT_VOICE_MUTE_CMD", default="")
PREFLIGHT_VOICE_MUTE_TIMEOUT_SECONDS = env.int("PREFLIGHT_VOICE_MUTE_TIMEOUT_SECONDS", default=180)
# Phase 8A closeout: the `learning_scrubber` check runs the scrubber over the
# labelled corpus. Empty = the committed `memory/tests/scrubber_corpus/`
# shipped with the backend; a missing directory makes the check a warning.
PREFLIGHT_SCRUBBER_CORPUS_DIR = env("PREFLIGHT_SCRUBBER_CORPUS_DIR", default="")
# Phase 9 closeout: the node checks. PREFLIGHT_NODE_GATEWAY_URL is the
# node-gateway's liveness URL (`http://node-gateway:8002/healthz` inside the
# compose network); empty = `node_gateway` is a warning and the gateway's key
# id is not compared. NODE_TLS_PIN_SHA256 (base64 or hex sha256 of the
# served certificate's SubjectPublicKeyInfo) and PREFLIGHT_NODE_TLS_URL (the
# public https/wss URL the proxy serves `/ws/node/v1` on): when both are
# set, `node_tls_pin` compares the served SPKI with the pin.
PREFLIGHT_NODE_GATEWAY_URL = env("PREFLIGHT_NODE_GATEWAY_URL", default="")
NODE_TLS_PIN_SHA256 = env("NODE_TLS_PIN_SHA256", default="")
PREFLIGHT_NODE_TLS_URL = env("PREFLIGHT_NODE_TLS_URL", default="")
# Phase 10 closeout: the date (ISO, YYYY-MM-DD) the owner recorded the
# ADR-014 evidence run of the escape suite under macOS `srt` (Mac session M3)
# in ADR-014's implementation notes. Empty = pending owner evaluation: the
# `node_escape_suite` preflight check is then a warning, never a failure.
NODE_ESCAPE_EVIDENCE_RECORDED_ON = env("NODE_ESCAPE_EVIDENCE_RECORDED_ON", default="")
# Phase 11 closeout: the one-line JSON result the nightly self-trigger run on
# the Mac writes (`friday-voice selftest --nightly --result-file`, Mac
# session M3), read by `voice_live_selftest`. Empty or missing = pending owner
# evaluation (a warning); a failed last run is a failure.
VOICE_LIVE_SELFTEST_RESULT_PATH = env("VOICE_LIVE_SELFTEST_RESULT_PATH", default="")
# The `node/friday-voice` checkout `friday_voice_conformance` hashes against
# its `conformance.lock`. Empty = `<repo>/node/friday-voice` next to the
# backend; absent (the backend image ships no `node/`) = a warning.
PREFLIGHT_FRIDAY_VOICE_DIR = env("PREFLIGHT_FRIDAY_VOICE_DIR", default="")

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

# --- HUD (Phase 6 branch 5, brief A8) ---
# `GET /api/v1/hud/events`: one bounded SSE stream per HUD tab. It re-reads
# its sections every HUD_STREAM_INTERVAL_SECONDS and closes itself after
# HUD_STREAM_MAX_SECONDS (the client reconnects after HUD_STREAM_RETRY_MS,
# re-running auth and every permission check), so a sync worker is never
# held indefinitely.
HUD_STREAM_INTERVAL_SECONDS = env.int("HUD_STREAM_INTERVAL_SECONDS", default=5)
HUD_STREAM_MAX_SECONDS = env.int("HUD_STREAM_MAX_SECONDS", default=55)
HUD_STREAM_RETRY_MS = env.int("HUD_STREAM_RETRY_MS", default=3000)
# Items per HUD list (pending approvals, today's tasks).
HUD_LIST_LIMIT = env.int("HUD_LIST_LIMIT", default=8)
# A request approved this recently counts as "tool running" on the HUD.
HUD_TOOL_RUNNING_SECONDS = env.int("HUD_TOOL_RUNNING_SECONDS", default=30)
# A provider's last call within this window decides its health status
# (ok / down); older or none is "unknown".
HUD_PROVIDER_WINDOW_MINUTES = env.int("HUD_PROVIDER_WINDOW_MINUTES", default=30)
# Where `manage.py preflight` leaves its last summary for the health panel
# (the shared cache; `ops` is a leaf no app imports, the key is the only
# coupling). Kept for HUD_PREFLIGHT_CACHE_SECONDS.
HUD_PREFLIGHT_CACHE_KEY = "ops:preflight:last"
HUD_PREFLIGHT_CACHE_SECONDS = env.int("HUD_PREFLIGHT_CACHE_SECONDS", default=7 * 24 * 3600)
# Phase 10 D12: `GET /api/v1/hud/terminal/<execution_id>/stream` polls the
# node output Redis stream this often (it never blocks on Redis); it shares
# HUD_STREAM_MAX_SECONDS / HUD_STREAM_RETRY_MS with the event stream.
HUD_TERMINAL_POLL_SECONDS = env.float("HUD_TERMINAL_POLL_SECONDS", default=0.5)

# --- Connectors (Phase 5 branch 4, ADR-010, brief D1-D6) ---

# Manifests are code (`connectors/manifests/`); these are only the runtime
# bounds. Server URLs and credentials are never settings: each manifest names
# its own env vars (URL on the Friday side, credential in the sidecar only).
# A read runs in the chat request thread, bounded by this timeout (D4).
CONNECTOR_READ_TIMEOUT_SECONDS = env.int("CONNECTOR_READ_TIMEOUT_SECONDS", default=10)
# What survives of a read (text + structuredContent), in characters -- the
# harness transcript cap. Anything longer is truncated and flagged (D4).
CONNECTOR_RESULT_MAX_CHARS = env.int("CONNECTOR_RESULT_MAX_CHARS", default=8000)
# Hard cap on the bytes of any single MCP HTTP response body, enforced while
# it streams in -- before parsing -- so a runaway server cannot exhaust the
# web or worker process within the timeout (D4). Over it: protocol_error.
CONNECTOR_RESPONSE_MAX_BYTES = env.int("CONNECTOR_RESPONSE_MAX_BYTES", default=1_048_576)
# A gated write's single send (D6), and the pin/health check before it.
CONNECTOR_WRITE_TIMEOUT_SECONDS = env.int("CONNECTOR_WRITE_TIMEOUT_SECONDS", default=30)
CONNECTOR_HEALTH_TIMEOUT_SECONDS = env.int("CONNECTOR_HEALTH_TIMEOUT_SECONDS", default=10)
# A write's claim lease: longer than the send timeout plus the pin check, so
# a live worker never loses its lease mid-call. A lease that expires anyway
# means the worker was lost -> `unknown_outcome`, never a re-send (D6).
CONNECTOR_WRITE_LEASE_SECONDS = env.int("CONNECTOR_WRITE_LEASE_SECONDS", default=120)
# n8n (Phase 12 D9-D12, docs/mcp/n8n.md). The relay sidecar's internal URL is
# NOT a setting: the manifest names `N8N_MCP_URL`, read from the environment
# at call time; the bearer lives only in the `n8n-relay` sidecar
# (`RELAY_BEARER_TOKEN`). The one tunable: how long an async read's one
# call may take (D11; the worker never retries it).
N8N_ASYNC_READ_TIMEOUT_SECONDS = env.int("N8N_ASYNC_READ_TIMEOUT_SECONDS", default=120)
# Google bridge (Phase 5 branch 8, brief D9). Credentials are NOT settings:
# `GOOGLE_OAUTH_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` are read from
# the environment at call time by `connectors.bridges.google` only (never
# stored, cached, audited or logged). The one tunable: how much of a mail's
# text body `mail_read` keeps (the whole read is still bounded by
# CONNECTOR_RESULT_MAX_CHARS).
GOOGLE_MAIL_BODY_MAX_CHARS = env.int("GOOGLE_MAIL_BODY_MAX_CHARS", default=6000)

# Home Assistant native bridge (Phase 5 branch 7, brief D8, R5). The base URL
# and the token are NOT settings: `connectors.bridges.home_assistant` reads
# `HA_BASE_URL` / `HA_TOKEN` from the environment at call time only. These
# two can only narrow the code-defined maximum (`bridges/home_assistant.py`
# `SERVICES`): unset/empty = the code maximum; names outside it are ignored.
HA_ALLOWED_DOMAINS = env.list("HA_ALLOWED_DOMAINS", default=[])
# Wake-on-LAN targets; empty = `wake_on_lan.send_magic_packet` is refused.
HA_WOL_ALLOWED_MACS = env.list("HA_WOL_ALLOWED_MACS", default=[])

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

# Per-agent container image (brief: "a single Friday-owned Dockerfile per
# supported agent, pinned to a specific, recorded base-image digest --
# never `latest`" -- see harness/docker/*.Dockerfile). Image *tags* are
# configured here rather than hardcoded so a reviewed digest bump
# (the brief's own "reviewed bump/rebuild cadence") is an env change, not
# a code change.
HARNESS_IMAGE_CLAUDE_CODE = env(
    "HARNESS_IMAGE_CLAUDE_CODE", default="friday-harness-claude-code:unbuilt"
)
HARNESS_IMAGE_CODEX_CLI = env("HARNESS_IMAGE_CODEX_CLI", default="friday-harness-codex-cli:unbuilt")
# Phase 8B branch 5 (brief D10): the Graphify AST-only image
# (harness/docker/graphify.Dockerfile, `graphifyy` hash-pinned). It runs
# with `--network none`, a read-only root and a size-capped tmpfs `/work`.
HARNESS_IMAGE_GRAPHIFY = env("HARNESS_IMAGE_GRAPHIFY", default="friday-harness-graphify:0.9.103")
HARNESS_GRAPHIFY_TMPFS_SIZE = env("HARNESS_GRAPHIFY_TMPFS_SIZE", default="1g")

# Docker network implementing the container policy's egress allowlist
# (brief: "no general internet access... enforced via Docker network
# policy"). Provisioning the actual restricted-egress network is runner
# setup, not application code -- this only names which network a launched
# container joins.
HARNESS_DOCKER_NETWORK = env("HARNESS_DOCKER_NETWORK", default="friday-harness-egress")

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
# definitions or permissions"): remote URLs recognized as "this Friday
# installation itself", compared via core.repo_identity's canonical
# normalizer, not exact string match -- so the https/ssh/scp-like forms of
# the same remote are all correctly recognized. Empty by default -- and since
# Phase 3 branch 5 an empty list REFUSES every implementation job (fail
# closed): the guard cannot tell whether a repo is Friday itself until the
# owner configures this installation's own remote(s) here.
HARNESS_SELF_REPO_IDENTIFIERS = [
    value.strip()
    for value in env.list("HARNESS_SELF_REPO_IDENTIFIERS", default=[])
    if value.strip()
]

# Harness job-distribution policy (Phase 5 D11; replaces the Phase 3
# `_SELECTABLE_*_AGENTS` sets in harness/services.py). Per job kind: the
# `default` agent used when the owner names none (agent_routing_reason
# "policy:default"), the owner-`selectable` agents ("owner_selected"), and
# for implementation the cross-vendor `reviewers` pairing implementer ->
# reviewer (review rows record "policy:cross_vendor"). The `codex_cli`
# implementation slot exists (its image and write-mode wrapper ship) but
# stays off until its write-mode vendor terms are verified -- add it to
# `selectable` AND give it a reviewer to turn it on. ADR-008 decision 3:
# a Django system check (harness.checks, `harness.E001`...) refuses to boot
# on any self-pairing, a selectable implementer without a reviewer, or an
# unknown agent. Not env-configurable: this is policy, changed in code
# review, not per deployment.
HARNESS_AGENT_POLICY = {
    "discovery": {
        "default": "claude_code",
        "selectable": ["claude_code"],
    },
    "implementation": {
        "default": "claude_code",
        "selectable": ["claude_code"],  # "codex_cli" slot: off
        "reviewers": {"claude_code": "codex_cli"},
    },
}

# How long a finished-but-undecided implementation worktree is kept on disk
# waiting for a Gate B (push approval) decision, across however long the
# owner takes and across a possible worker restart (brief §boundary,
# "worktree lifetime across the Gate-B wait"). The recovery scan (branch 4)
# sweeps and fails closed any worktree past this deadline whose job never
# reached a terminal state -- a backstop, not the primary cleanup path
# (every terminal transition cleans up its own worktree immediately).
HARNESS_WORKTREE_RETENTION_HOURS = env.int("HARNESS_WORKTREE_RETENTION_HOURS", default=72)

# --- Harness v3 drivers (Phase 10 brief D10, branch 6) ---
#
# A driver replaces only the "run the CLI once" step of an implementation
# run with a long-lived, bidirectional session (Codex app-server JSON-RPC
# over the container's stdio). Off by default on both switches: a non-`cli`
# driver needs HARNESS_DRIVERS_ENABLED=true (system check harness.E002) AND
# the owner's third-party terms check (d4) recorded as a date in
# HARNESS_DRIVER_TERMS_CHECKED_ON (harness.E003). Discovery and review keep
# the CLI path in this phase. Allowed values per agent live in
# `harness.drivers.DRIVER_CHOICES`.
HARNESS_DRIVERS_ENABLED = env.bool("HARNESS_DRIVERS_ENABLED", default=False)
HARNESS_DRIVER_CODEX = env("HARNESS_DRIVER_CODEX", default="cli")
# Branch 7: cli | agent_sdk (the Claude Agent SDK through the in-image
# bridge, harness/docker/claude_agent_bridge.py).
HARNESS_DRIVER_CLAUDE = env("HARNESS_DRIVER_CLAUDE", default="cli")
HARNESS_DRIVER_TERMS_CHECKED_ON = env("HARNESS_DRIVER_TERMS_CHECKED_ON", default="")
# The one pin of the Codex app-server driver: must equal the Dockerfile
# `ARG CODEX_CLI_VERSION` and the schema snapshot's recorded version under
# harness/drivers/codex_schema/<version>/ (system check harness.E004).
HARNESS_CODEX_APP_SERVER_VERSION = env("HARNESS_CODEX_APP_SERVER_VERSION", default="0.1.0")
# The one pin of the Claude Agent SDK driver: must equal the Dockerfile
# `ARG CLAUDE_AGENT_SDK_VERSION` and the SDK surface snapshot's version under
# harness/drivers/claude_sdk_surface/<version>/ (harness.E004); the bridge
# reports the installed SDK version and the driver refuses a mismatch.
HARNESS_CLAUDE_AGENT_SDK_VERSION = env("HARNESS_CLAUDE_AGENT_SDK_VERSION", default="0.1.0")
# How often a driver waiting on a `harness_agent_action` approval re-reads
# the ApprovalRequest row (the source of truth); the Redis wake-up hint
# written on approve only shortens the wait.
HARNESS_AGENT_ACTION_POLL_SECONDS = env.int("HARNESS_AGENT_ACTION_POLL_SECONDS", default=2)
# Escalation cards per job (brief R5, approval fatigue): beyond this the
# driver declines without creating another ApprovalRequest.
HARNESS_DRIVER_MAX_ESCALATIONS = env.int("HARNESS_DRIVER_MAX_ESCALATIONS", default=20)
# Token brake for driver runs: the Codex app-server reports tokens but no
# cost, so HARNESS_MAX_JOB_COST cannot bound it; a run whose summed turn
# tokens exceed this fails `budget_exceeded`.
HARNESS_DRIVER_MAX_TOKENS = env.int("HARNESS_DRIVER_MAX_TOKENS", default=2_000_000)

# --- Deploys (Phase 5 branch 6, ADR-010 decision 7, brief D10) ---

# The private key the runner uses for the forced-command deploy account on
# every DeployTarget, resolved from env only (never on a row, a payload or a
# log). Empty by default: with no key configured every deploy run and status
# refresh fails closed (`ssh_key_missing`) -- there is no ambient fallback
# (no SSH agent, no ~/.ssh default key; see deploys/ssh.py).
DEPLOY_SSH_KEY_PATH = env("DEPLOY_SSH_KEY_PATH", default="")
# Per-invocation timeouts of the system `ssh` client. The deploy/rollback
# verbs run the target's own build + restart commands, hence the long one.
DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS = env.int("DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS", default=10)
DEPLOY_SSH_STATUS_TIMEOUT_SECONDS = env.int("DEPLOY_SSH_STATUS_TIMEOUT_SECONDS", default=60)
DEPLOY_SSH_HEALTH_TIMEOUT_SECONDS = env.int("DEPLOY_SSH_HEALTH_TIMEOUT_SECONDS", default=30)
DEPLOY_SSH_DEPLOY_TIMEOUT_SECONDS = env.int("DEPLOY_SSH_DEPLOY_TIMEOUT_SECONDS", default=900)
# The optional `health_url` GET (no redirects, no proxies from env).
DEPLOY_HEALTH_URL_TIMEOUT_SECONDS = env.int("DEPLOY_HEALTH_URL_TIMEOUT_SECONDS", default=10)
# Health checks are spaced evenly across a target's health window: window /
# this many. At least 3, so three consecutive failures always span most of
# the window (ADR-010: one blip never rolls back).
DEPLOY_HEALTH_CHECKS_PER_WINDOW = env.int("DEPLOY_HEALTH_CHECKS_PER_WINDOW", default=4)
# A claimed run's lease, renewed at every step. Must exceed the longest
# single step (the deploy verb's timeout) with room to spare, or the
# recovery scan could declare a live run lost (asserted in deploys.tasks).
DEPLOY_RUN_LEASE_SECONDS = env.int("DEPLOY_RUN_LEASE_SECONDS", default=1200)
# A `pending` run still unclaimed this long after its last update is
# re-dispatched by the recovery scan (its on_commit publish was lost).
DEPLOY_PENDING_RECOVERY_GRACE_SECONDS = env.int("DEPLOY_PENDING_RECOVERY_GRACE_SECONDS", default=60)

# --- Telegram bot (MVP 9 §4) ---

# Bot API token from @BotFather -- used only server-side by
# integrations/telegram_client.py to call the Bot API. Never the same
# value as TELEGRAM_WEBHOOK_SECRET below.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")

# --- Proactive runs (Phase 12 D2-D5, ADR-016) ---
# A probe slower than this is flagged in its run row and in preflight
# (measured, not interrupted: probes are DB reads).
PROACTIVE_PROBE_TIMEOUT_MS = env.int("PROACTIVE_PROBE_TIMEOUT_MS", default=2000)
# The hard bound: every statement of a probe's transaction is cancelled
# after this long (Postgres `SET LOCAL statement_timeout`); the probe is
# recorded as `probe_timeout` and the run continues. 0 disables it.
PROACTIVE_PROBE_STATEMENT_TIMEOUT_MS = env.int(
    "PROACTIVE_PROBE_STATEMENT_TIMEOUT_MS", default=10000
)
# Read or dismissed notices are hard-deleted after this many days
# (`proactive.tasks.prune`, daily).
PROACTIVE_NOTICE_RETENTION_DAYS = env.int("PROACTIVE_NOTICE_RETENTION_DAYS", default=30)
# The D5 delta baseline keeps at most this many item keys per probe.
PROACTIVE_PROBE_MAX_KEYS = env.int("PROACTIVE_PROBE_MAX_KEYS", default=200)
# The only Celery tasks an unattended run may dispatch (D2): the proactive
# tasks themselves. Not a capability allow list -- no setting widens what a
# run may do.
PROACTIVE_UNATTENDED_TASKS = tuple(
    env.list(
        "PROACTIVE_UNATTENDED_TASKS",
        default=["proactive.tasks.heartbeat", "proactive.tasks.prune"],
    )
)
# Phase 12 D6: the spoken digest text is bounded (characters, after the
# Markdown/invisible-formatting strip); the template fallback obeys it too.
PROACTIVE_DIGEST_MAX_CHARS = env.int("PROACTIVE_DIGEST_MAX_CHARS", default=900)
# The `ai_core.budgets` probe reports a budget window at or above this
# share of its limit (D1: "any budget >= 80 %").
PROACTIVE_BUDGET_WARN_PERCENT = env.int("PROACTIVE_BUDGET_WARN_PERCENT", default=80)

# --- SKILL.md instruction skills (Phase 12 D13-D15, ADR-016 decision 4) ---
# A skill body is plain text up to this many characters (after invisible
# formatting is stripped); a longer one is refused (`body_too_large`).
SKILLS_BODY_MAX_CHARS = env.int("SKILLS_BODY_MAX_CHARS", default=16000)
# One upload (a SKILL.md or a .zip bundle) is at most this many bytes; also
# enforced on the request body before the upload is parsed.
SKILLS_BUNDLE_MAX_BYTES = env.int("SKILLS_BUNDLE_MAX_BYTES", default=1024 * 1024)
# At most this many active (or stale) skills per workspace (D14).
SKILLS_MAX_ACTIVE = env.int("SKILLS_MAX_ACTIVE", default=50)
# Pending proposals at any time, per workspace and proposer (D14, D16, D17).
SKILLS_MAX_PENDING_CURATOR = env.int("SKILLS_MAX_PENDING_CURATOR", default=5)
SKILLS_MAX_PENDING_PLAYBOOK = env.int("SKILLS_MAX_PENDING_PLAYBOOK", default=3)
# The `skills.match` call's gateway timeout (D15): a voice turn's budget is
# small (it adds to the spoken reply's latency); a timeout means "no skill".
SKILLS_MATCH_TIMEOUT_MS = env.int("SKILLS_MATCH_TIMEOUT_MS", default=1500)
SKILLS_MATCH_VOICE_TIMEOUT_MS = env.int("SKILLS_MATCH_VOICE_TIMEOUT_MS", default=600)
# The injected skill block (body plus the references the body names) is
# bounded to this many characters.
SKILLS_CONTEXT_MAX_CHARS = env.int("SKILLS_CONTEXT_MAX_CHARS", default=8000)

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
    # httpx logs every request URL at INFO -- for the Google bridge that is
    # the owner's mail search query, message ids and calendar windows
    # (Phase 5 branch 8). Warnings and errors only.
    "loggers": {
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
    },
}
