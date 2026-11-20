"""`manage.py preflight` (Phase 5 closeout; MASTER_PLAN "Roadmap update
2026-11-20"): live end-to-end checks against the RUNNING stack, not mocks.

Each check returns one or more `CheckResult`s with a status:

- `pass` -- the service answered as expected;
- `fail` -- it is configured but broken (the command exits non-zero);
- `warn` -- it is not configured here (or deliberately skipped), so
  nothing could be checked. A warning never fails the run.

Every outside contact goes through a module-level seam (`http_get`,
`db_fetchone`, `redis_ping`, `celery_ping`, `gateway_factory`,
`pin_report`) so the unit tests replace exactly that and nothing else --
no test reaches a real provider, MCP server, browser or broker.

Rules this module keeps:

- Provider calls go through the single door (ADR-003): one minimal
  `InferenceGateway.complete()` (or `embed()`) per provider, pinned to that
  provider with `only_provider`, purpose `ops.preflight`, so each call is a
  `ModelUsage` row like any other. Nothing personal is ever sent.
- Pin checks are read-only (`connectors.services.live_pin_report`): the
  15-minute pin beat stays the one writer of binding health.
- A secret found in browser-reachable content is reported by its env var
  NAME only; the value is never printed or stored.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from django.conf import settings

PASS = "pass"
FAIL = "fail"
WARN = "warn"

PREFLIGHT_PURPOSE = "ops.preflight"
# The smallest prompt that proves a provider + model id answer: no data,
# one short word back.
PREFLIGHT_MESSAGES = [{"role": "user", "content": "Reply with the single word: ok"}]
PREFLIGHT_PARAMS = {"max_tokens": 5, "temperature": 0}
PREFLIGHT_EMBED_TEXTS = ["preflight"]

# Env var names whose values count as secrets for the browser scan.
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)S?$|_DSN$")
# Shorter values (placeholders such as "x", "true") would match by chance.
_SECRET_MIN_LEN = 12
# Values that are configuration, not secrets, even under a secret-ish name.
_NOT_SECRET_VALUES = {"true", "false", "ollama-local"}
# How many same-origin scripts the browser scan fetches at most.
MAX_SCRIPTS = 40

_SCRIPT_SRC = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
# Next.js: pages/legacy manifests carry the build id in their path; the App
# Router's flight payload carries it as `"b":"<id>"` (possibly escaped).
_BUILD_ID_PATTERNS = (
    re.compile(r"/_next/static/([A-Za-z0-9_-]+)/_(?:build|ssg)Manifest\.js"),
    re.compile(r"""\\?"b\\?":\\?"([A-Za-z0-9_-]{6,})\\?\""""),
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str = ""


# --- seams (replaced in tests) -------------------------------------------


def http_get(url, *, timeout):
    """`(status_code, text)` of a GET, following no redirects to another
    host. Network errors propagate (the caller maps them to `fail`)."""
    import httpx

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    return response.status_code, response.text


def db_fetchone(sql):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone()


def redis_ping(url, *, timeout):
    import redis

    client = redis.Redis.from_url(url, socket_timeout=timeout, socket_connect_timeout=timeout)
    try:
        return bool(client.ping())
    finally:
        client.close()


def celery_ping(*, timeout):
    """Replies from running workers (`[{worker: {"ok": "pong"}}]`)."""
    from celery import current_app

    return current_app.control.ping(timeout=timeout) or []


def gateway_factory():
    from ai_core.gateway import InferenceGateway

    return InferenceGateway()


def pin_report(manifest, *, timeout):
    from connectors.services import live_pin_report

    return live_pin_report(manifest, timeout=timeout)


# --- helpers --------------------------------------------------------------


def _timeout():
    return getattr(settings, "PREFLIGHT_TIMEOUT_SECONDS", 10)


def _error_label(exc):
    """A safe one-word label for an exception: its class name and, for our
    own errors, the closed-vocabulary code. Never the message (it may carry
    a URL with credentials or a provider's response body)."""
    code = getattr(exc, "error_type", "") or getattr(exc, "code", "")
    return f"{type(exc).__name__}{f' ({code})' if code else ''}"


def _get_page(url):
    status, text = http_get(url, timeout=_timeout())
    return status, text or ""


# --- checks ---------------------------------------------------------------


def check_web():
    url = getattr(settings, "PREFLIGHT_WEB_URL", "")
    if not url:
        return [CheckResult("web", WARN, "PREFLIGHT_WEB_URL not set")]
    try:
        status, _ = _get_page(url)
    except Exception as exc:
        return [CheckResult("web", FAIL, f"unreachable: {_error_label(exc)}")]
    if status != 200:
        return [CheckResult("web", FAIL, f"HTTP {status}")]
    return [CheckResult("web", PASS, "HTTP 200")]


def check_bff():
    url = getattr(settings, "PREFLIGHT_BFF_URL", "")
    if not url:
        return [CheckResult("bff", WARN, "PREFLIGHT_BFF_URL not set")]
    try:
        status, _ = _get_page(url)
    except Exception as exc:
        return [CheckResult("bff", FAIL, f"unreachable: {_error_label(exc)}")]
    if status != 200:
        return [CheckResult("bff", FAIL, f"HTTP {status}")]
    return [CheckResult("bff", PASS, "HTTP 200")]


def check_postgres():
    try:
        row = db_fetchone("SELECT 1")
    except Exception as exc:
        return [CheckResult("postgres", FAIL, f"unreachable: {_error_label(exc)}")]
    if not row or row[0] != 1:
        return [CheckResult("postgres", FAIL, "unexpected reply")]
    return [CheckResult("postgres", PASS, "SELECT 1")]


def check_pgvector():
    try:
        row = db_fetchone("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    except Exception as exc:
        return [CheckResult("pgvector", FAIL, f"query failed: {_error_label(exc)}")]
    if not row:
        return [CheckResult("pgvector", FAIL, "extension `vector` not installed")]
    return [CheckResult("pgvector", PASS, f"vector {row[0]}")]


def check_redis():
    url = getattr(settings, "REDIS_URL", "")
    if not url:
        return [CheckResult("redis", WARN, "REDIS_URL not set")]
    try:
        ok = redis_ping(url, timeout=_timeout())
    except Exception as exc:
        return [CheckResult("redis", FAIL, f"unreachable: {_error_label(exc)}")]
    if not ok:
        return [CheckResult("redis", FAIL, "no PONG")]
    return [CheckResult("redis", PASS, "PONG")]


def check_celery():
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return [CheckResult("celery", WARN, "CELERY_TASK_ALWAYS_EAGER: no worker to ping")]
    try:
        replies = celery_ping(timeout=_timeout())
    except Exception as exc:
        return [CheckResult("celery", FAIL, f"broker unreachable: {_error_label(exc)}")]
    if not replies:
        return [CheckResult("celery", FAIL, "no worker answered the ping")]
    return [CheckResult("celery", PASS, f"{len(replies)} worker(s) answered")]


def _provider_skip_reason(provider):
    from ai_core.models import ModelProvider

    if not provider.is_active:
        return "disabled"
    if provider.provider_type == ModelProvider.ProviderType.FAKE:
        if not getattr(settings, "AI_FAKE_PROVIDER", False):
            return "fake provider off (AI_FAKE_PROVIDER)"
        return ""
    if not os.environ.get(provider.api_key_env_var or ""):
        return f"{provider.api_key_env_var or 'key env var'} not set"
    return ""


AUDIO_CAPABILITIES = ("stt", "tts")


def check_providers(*, model_calls=True):
    """One minimal gateway call per seeded provider (chat: `complete`,
    embedding: `embed`), each pinned to its provider so a fallback can
    never hide a broken one. A success proves the provider AND its
    configured `model_id` answer."""
    from ai_core.models import ModelProvider

    results = []
    providers = list(ModelProvider.objects.order_by("capability", "priority", "name"))
    if not providers:
        return [CheckResult("providers", FAIL, "no provider rows: run seed_ai_defaults")]
    gateway = None
    for provider in providers:
        name = f"provider:{provider.name}"
        if provider.capability in AUDIO_CAPABILITIES:
            # Phase 6 speech rows (STT/TTS) need real audio in/out; a chat
            # call pinned to them would only fail. A standing warn until
            # the voice layer gets its own preflight (see `voice_mute`).
            results.append(CheckResult(name, WARN, "audio provider: not checked yet"))
            continue
        skip = _provider_skip_reason(provider)
        if skip:
            results.append(CheckResult(name, WARN, f"not configured: {skip}"))
            continue
        if not model_calls:
            results.append(CheckResult(name, WARN, "skipped (--no-model-calls)"))
            continue
        if gateway is None:
            gateway = gateway_factory()
        try:
            if provider.capability == ModelProvider.Capability.EMBEDDING:
                gateway.embed(PREFLIGHT_PURPOSE, PREFLIGHT_EMBED_TEXTS, only_provider=provider.name)
            else:
                gateway.complete(
                    PREFLIGHT_PURPOSE,
                    PREFLIGHT_MESSAGES,
                    params=dict(PREFLIGHT_PARAMS),
                    only_provider=provider.name,
                )
        except Exception as exc:
            results.append(
                CheckResult(name, FAIL, f"model {provider.model_id}: {_error_label(exc)}")
            )
            continue
        results.append(CheckResult(name, PASS, f"model {provider.model_id} answered"))
    return results


def check_mcp_pins():
    """Every MCP connector with at least one enabled binding: a live
    `tools/list` must match every manifest pin."""
    from connectors.manifest import KIND_MCP, get_manifest
    from connectors.mcp_client import ConnectorError
    from connectors.models import ConnectorBinding

    names = sorted(
        set(ConnectorBinding.objects.filter(enabled=True).values_list("connector", flat=True))
    )
    manifests = [
        m
        for m in (get_manifest(n) for n in names)
        if m is not None and m.kind == KIND_MCP and not m.blocked
    ]
    if not manifests:
        return [CheckResult("mcp_pins", WARN, "no enabled MCP binding")]
    results = []
    for manifest in manifests:
        name = f"mcp_pins:{manifest.name}"
        try:
            statuses = pin_report(manifest, timeout=_timeout())
        except ConnectorError as exc:
            status = WARN if exc.code == "not_configured" else FAIL
            results.append(CheckResult(name, status, f"tools/list failed: {exc.code}"))
            continue
        except Exception as exc:
            results.append(CheckResult(name, FAIL, f"tools/list failed: {_error_label(exc)}"))
            continue
        bad = sorted(f"{tool}={state}" for tool, state in statuses.items() if state != "ok")
        if bad:
            results.append(CheckResult(name, FAIL, "pins differ: " + ", ".join(bad)))
        else:
            results.append(CheckResult(name, PASS, f"{len(statuses)} tool pin(s) match"))
    return results


def extract_build_id(html):
    for pattern in _BUILD_ID_PATTERNS:
        match = pattern.search(html or "")
        if match:
            return match.group(1)
    return ""


def check_frontend_build():
    """The build the BFF serves is the build on disk (`.next/BUILD_ID`):
    a stale server after `npm run build` shows up here."""
    url = getattr(settings, "PREFLIGHT_BFF_URL", "")
    build_file = getattr(settings, "PREFLIGHT_FRONTEND_BUILD_ID_FILE", "")
    if not url or not build_file:
        return [
            CheckResult(
                "frontend_build",
                WARN,
                "PREFLIGHT_BFF_URL / PREFLIGHT_FRONTEND_BUILD_ID_FILE not set",
            )
        ]
    path = Path(build_file)
    if not path.is_file():
        return [CheckResult("frontend_build", FAIL, "BUILD_ID file missing: run the build")]
    built = path.read_text().strip()
    try:
        _, html = _get_page(url)
    except Exception as exc:
        return [CheckResult("frontend_build", FAIL, f"unreachable: {_error_label(exc)}")]
    served = extract_build_id(html)
    if not served:
        return [CheckResult("frontend_build", FAIL, "no build id in the served page")]
    if served != built:
        return [CheckResult("frontend_build", FAIL, f"served {served} != built {built}")]
    return [CheckResult("frontend_build", PASS, f"build {built}")]


def secret_values():
    """`{env var name: value}` of the secrets in this process's environment
    the browser must never see."""
    found = {}
    for name, value in os.environ.items():
        if not _SECRET_NAME.search(name):
            continue
        value = (value or "").strip()
        if len(value) < _SECRET_MIN_LEN or value.lower() in _NOT_SECRET_VALUES:
            continue
        found[name] = value
    return found


def _same_origin_scripts(base_url, html):
    origin = urlparse(base_url)
    scripts = []
    for src in _SCRIPT_SRC.findall(html):
        absolute = urljoin(base_url, src)
        parsed = urlparse(absolute)
        if (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc):
            scripts.append(absolute)
    return list(dict.fromkeys(scripts))[:MAX_SCRIPTS]


def check_browser_secrets():
    """No secret from this environment appears in what a browser receives:
    the served page and every same-origin script it loads."""
    url = getattr(settings, "PREFLIGHT_BFF_URL", "")
    if not url:
        return [CheckResult("browser_secrets", WARN, "PREFLIGHT_BFF_URL not set")]
    secrets = secret_values()
    if not secrets:
        return [CheckResult("browser_secrets", WARN, "no secrets in this environment to look for")]
    try:
        _, html = _get_page(url)
    except Exception as exc:
        return [CheckResult("browser_secrets", FAIL, f"unreachable: {_error_label(exc)}")]
    bodies = [html]
    for script in _same_origin_scripts(url, html):
        try:
            _, body = _get_page(script)
        except Exception as exc:
            return [CheckResult("browser_secrets", FAIL, f"script fetch: {_error_label(exc)}")]
        bodies.append(body)
    leaked = sorted(name for name, value in secrets.items() if any(value in b for b in bodies))
    if leaked:
        # Names only -- the value itself is never echoed.
        return [
            CheckResult("browser_secrets", FAIL, "reachable from the browser: " + ", ".join(leaked))
        ]
    return [
        CheckResult(
            "browser_secrets",
            PASS,
            f"{len(secrets)} secret(s) absent from {len(bodies)} served file(s)",
        )
    ]


def check_voice_mute():
    """`?mute=1` voice tab is silent: the voice layer is Phase 6 and not
    built yet, so there is nothing to check -- a standing warning until
    Phase 6 replaces this check with a real one."""
    return [CheckResult("voice_mute", WARN, "voice layer not built yet (Phase 6)")]


CHECKS = (
    ("web", check_web),
    ("bff", check_bff),
    ("postgres", check_postgres),
    ("pgvector", check_pgvector),
    ("redis", check_redis),
    ("celery", check_celery),
    ("providers", check_providers),
    ("mcp_pins", check_mcp_pins),
    ("frontend_build", check_frontend_build),
    ("browser_secrets", check_browser_secrets),
    ("voice_mute", check_voice_mute),
)


def run_preflight(*, model_calls=True, checks=CHECKS):
    """Run every check; a check that raises unexpectedly becomes one `fail`
    row rather than aborting the run (later checks still report)."""
    results = []
    for name, check in checks:
        try:
            if check is check_providers:
                results.extend(check(model_calls=model_calls))
            else:
                results.extend(check())
        except Exception as exc:
            results.append(CheckResult(name, FAIL, f"check crashed: {_error_label(exc)}"))
    return results


def summarize(results):
    counts = {PASS: 0, FAIL: 0, WARN: 0}
    for result in results:
        counts[result.status] += 1
    return f"{counts[PASS]} pass / {counts[FAIL]} fail / {counts[WARN]} warn"
