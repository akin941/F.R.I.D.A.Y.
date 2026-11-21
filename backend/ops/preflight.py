"""`manage.py preflight` (Phase 5 closeout; MASTER_PLAN "Roadmap update
2026-11-20"): live end-to-end checks against the RUNNING stack, not mocks.

Each check returns one or more `CheckResult`s with a status:

- `pass` -- the service answered as expected;
- `fail` -- it is configured but broken (the command exits non-zero);
- `warn` -- it is not configured here (or deliberately skipped), so
  nothing could be checked. A warning never fails the run.

Every outside contact goes through a module-level seam (`http_get`,
`db_fetchone`, `redis_ping`, `celery_ping`, `gateway_factory`,
`pin_report`, `run_command`) so the unit tests replace exactly that and
nothing else -- no test reaches a real provider, MCP server, browser or
broker.

Phase 6 closeout (brief A7) adds the voice checks: one STT call on a
fixture WAV and one TTS call on a fixed sentence per speech provider row
(pinned, purpose `ops.preflight`, like the chat/embedding rows), the
speech rows / TTS routes / audio budget configuration (`voice_providers`,
`voice_routes`, `voice_budget`), and `voice_mute` -- a `?mute=1` voice tab
is silent, proven by a real browser run of `e2e/voice-mute.e2e.ts`
(`PREFLIGHT_VOICE_MUTE_CMD`).

Phase 8A closeout adds the learning checks (`check_learning`, read-only, no
model call): `learning_sources` (the four ingestion sources seeded, which
are enabled, mapped, paused), `learning_beat` (the producer, nightly,
recovery and TTL tasks on the right beat and registered), `learning_scrubber`
(the scrubber self-test over the labelled corpus: 100% secrets recall or
`fail`), `learning_budget` (an active budget covers each learning purpose)
and `learning_last_run` (the latest night's episode-extraction jobs).

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

import json
import math
import os
import re
import shlex
import struct
from collections import Counter
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
# Phase 6 closeout (A7): the speech round trip. The STT fixture is a 1 s
# 440 Hz tone in D3's upload shape (16 kHz mono PCM16), built in memory --
# not a recording, nothing personal, never written to disk. Its transcript
# is irrelevant (and never printed): the check proves the provider and its
# `model_id` accept the wire format and answer. The TTS sentence is fixed.
PREFLIGHT_WAV_SECONDS = 1.0
PREFLIGHT_WAV_HZ = 440
PREFLIGHT_TTS_TEXT = "Friday preflight."

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


def beat_schedules():
    """Both beats' schedules as `config.celery` publishes them
    (`{"worker": {...}, "runner": {...}}`) and the registered task names.
    `ops` may not import `config`, so this reads the Celery app's conf."""
    from celery import current_app

    current_app.loader.import_default_modules()
    schedules = current_app.conf.get("friday_beat_schedules") or {}
    return schedules, set(current_app.tasks)


def gateway_factory():
    from ai_core.gateway import InferenceGateway

    return InferenceGateway()


def pin_report(manifest, *, timeout):
    from connectors.services import live_pin_report

    return live_pin_report(manifest, timeout=timeout)


def run_command(argv, *, timeout):
    """Exit code of `argv` (no shell). Its output is captured and dropped:
    a browser run's log can echo URLs, cookies or tokens, so nothing of it
    reaches the preflight report. `TimeoutExpired`/`OSError` propagate."""
    import subprocess

    completed = subprocess.run(  # noqa: S603 -- argv from the owner's own setting, no shell
        argv, capture_output=True, timeout=timeout, check=False
    )
    return completed.returncode


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
    embedding: `embed`, Phase 6: STT `transcribe` on the fixture WAV, TTS
    `synthesize` on the fixed sentence), each pinned to its provider so a
    fallback can never hide a broken one. A success proves the provider AND
    its configured `model_id` answer."""
    from ai_core.models import ModelProvider

    results = []
    providers = list(ModelProvider.objects.order_by("capability", "priority", "name"))
    if not providers:
        return [CheckResult("providers", FAIL, "no provider rows: run seed_ai_defaults")]
    gateway = None
    for provider in providers:
        name = f"provider:{provider.name}"
        skip = _provider_skip_reason(provider)
        if not skip and provider.capability in AUDIO_CAPABILITIES:
            # D5: audio is always personal data, so the gateway would refuse
            # a disallowed row anyway -- the owner's d2 toggle is off.
            skip = "" if provider.personal_data_allowed else "not personal_data_allowed (d2)"
        if skip:
            results.append(CheckResult(name, WARN, f"not configured: {skip}"))
            continue
        if not model_calls:
            results.append(CheckResult(name, WARN, "skipped (--no-model-calls)"))
            continue
        if gateway is None:
            gateway = gateway_factory()
        if provider.capability in AUDIO_CAPABILITIES:
            results.append(_check_speech_provider(gateway, provider))
            continue
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


def preflight_wav():
    """The STT fixture (see `PREFLIGHT_WAV_SECONDS`): a quiet sine tone as a
    16 kHz mono PCM16 WAV, deterministic, in memory."""
    from ai_core.audio import STT_SAMPLE_RATE, pcm_to_wav

    frames = int(PREFLIGHT_WAV_SECONDS * STT_SAMPLE_RATE)
    samples = (
        int(8000 * math.sin(2 * math.pi * PREFLIGHT_WAV_HZ * i / STT_SAMPLE_RATE))
        for i in range(frames)
    )
    pcm = struct.pack(f"<{frames}h", *samples)
    return pcm_to_wav(pcm, sample_rate=STT_SAMPLE_RATE)


def _route_language_for(provider_name):
    """The TTS route language (A2) served by this row, so its pinned call
    uses the voice the route configures; "" when no route names it."""
    routes = getattr(settings, "VOICE_TTS_ROUTES", None) or {}
    for language, route in sorted(routes.items()):
        if (route or {}).get("provider") == provider_name:
            return language
    return ""


def _check_speech_provider(gateway, provider):
    """STT: the fixture WAV through `transcribe()`; TTS: the fixed sentence
    through `synthesize()` and its audio through D3's output rule (the same
    `normalize_tts_wav` the speech endpoint applies). Both pinned, purpose
    `ops.preflight`, one `ModelUsage` row each. Neither the transcript nor
    the audio is printed or kept."""
    from ai_core.audio import InvalidAudio, normalize_tts_wav
    from ai_core.models import ModelProvider

    name = f"provider:{provider.name}"
    model = f"model {provider.model_id}"
    if provider.capability == ModelProvider.Capability.STT:
        try:
            gateway.transcribe(
                PREFLIGHT_PURPOSE,
                preflight_wav(),
                content_type="audio/wav",
                params={"language": getattr(settings, "VOICE_STT_LANGUAGE", "")},
                only_provider=provider.name,
            )
        except Exception as exc:
            return CheckResult(name, FAIL, f"{model}: {_error_label(exc)}")
        return CheckResult(name, PASS, f"{model} transcribed the fixture WAV")
    language = _route_language_for(provider.name)
    try:
        result = gateway.synthesize(
            PREFLIGHT_PURPOSE,
            PREFLIGHT_TTS_TEXT,
            params={"language": language} if language else {},
            only_provider=provider.name,
        )
    except Exception as exc:
        return CheckResult(name, FAIL, f"{model}: {_error_label(exc)}")
    try:
        _, info = normalize_tts_wav(
            result.audio, max_seconds=getattr(settings, "VOICE_TTS_MAX_SECONDS", 60)
        )
    except InvalidAudio as exc:
        return CheckResult(name, FAIL, f"{model}: unusable audio ({exc.reason})")
    route = f" (route {language})" if language else ""
    return CheckResult(
        name, PASS, f"{model} synthesized {info.seconds:.1f}s at {info.sample_rate} Hz{route}"
    )


def _speech_row_skip(provider):
    """Why the gateway would never select this speech row ("" = eligible)."""
    skip = _provider_skip_reason(provider)
    if skip:
        return skip
    return "" if provider.personal_data_allowed else "not personal_data_allowed"


def _check_voice_providers(rows):
    missing = [c.upper() for c in AUDIO_CAPABILITIES if not rows[c]]
    if missing:
        return CheckResult(
            "voice_providers", FAIL, f"no {'/'.join(missing)} provider rows: run seed_ai_defaults"
        )
    eligible = {c: [p.name for p in rows[c] if not _speech_row_skip(p)] for c in AUDIO_CAPABILITIES}
    unusable = [c.upper() for c in AUDIO_CAPABILITIES if not eligible[c]]
    selectable = set(eligible["stt"]) | set(eligible["tts"])
    cloud_on = sorted(
        p.name for c in AUDIO_CAPABILITIES for p in rows[c] if p.is_priced and p.name in selectable
    )
    note = f"; cloud audio enabled: {', '.join(cloud_on)}" if cloud_on else ""
    if unusable:
        return CheckResult(
            "voice_providers",
            WARN,
            f"no eligible {'/'.join(unusable)} provider (VOICE_API_KEY / AI_FAKE_PROVIDER){note}",
        )
    return CheckResult(
        "voice_providers",
        PASS,
        f"stt: {', '.join(eligible['stt'])}; tts: {', '.join(eligible['tts'])}{note}",
    )


def _check_voice_routes(tts_rows):
    routes = getattr(settings, "VOICE_TTS_ROUTES", None) or {}
    if not routes:
        return CheckResult("voice_routes", WARN, "VOICE_TTS_ROUTES is empty")
    broken, idle, ok = [], [], []
    for language, route in sorted(routes.items()):
        target = (route or {}).get("provider") or ""
        row = tts_rows.get(target)
        if row is None:
            broken.append(f"{language} -> {target or '(none)'}")
        elif _speech_row_skip(row):
            idle.append(f"{language} -> {target} ({_speech_row_skip(row)})")
        else:
            ok.append(f"{language} -> {target}")
    # The gateway falls back to priority order for both (A2), so voice still
    # works: a warning that names what the route meant, never a failure.
    problems = []
    if broken:
        problems.append("no TTS row for route: " + ", ".join(broken))
    if idle:
        problems.append("routed row not eligible: " + ", ".join(idle))
    if problems:
        return CheckResult("voice_routes", WARN, "; ".join(problems) + " (priority fallback)")
    return CheckResult("voice_routes", PASS, ", ".join(ok))


def _describe_budget(budget):
    parts = [
        f"{window} {limit.normalize():f} USD"
        for window, limit in (
            ("monthly", budget.monthly_limit_usd),
            ("daily", budget.daily_limit_usd),
            ("session", budget.session_limit_usd),
        )
        if limit is not None
    ]
    return f"{budget.name}: {', '.join(parts) or 'no caps'}"


def _check_voice_budget():
    from ai_core.budgets import budgets_for

    purposes = tuple(getattr(settings, "AI_AUDIO_PURPOSES", ()) or ())
    if not purposes:
        return CheckResult("voice_budget", FAIL, "AI_AUDIO_PURPOSES is empty")
    covers = {p: budgets_for(p) for p in purposes}
    uncovered = [p for p in purposes if not covers[p]]
    if uncovered:
        return CheckResult(
            "voice_budget",
            FAIL,
            f"no active budget covers {', '.join(uncovered)}: run seed_ai_defaults",
        )
    budgets = {b.pk: b for found in covers.values() for b in found}
    described = "; ".join(
        _describe_budget(b) for b in sorted(budgets.values(), key=lambda b: b.name)
    )
    uncapped = [p for p in purposes if all(b.monthly_limit_usd is None for b in covers[p])]
    if uncapped:
        return CheckResult(
            "voice_budget", WARN, f"no monthly cap on {', '.join(uncapped)}: {described}"
        )
    return CheckResult("voice_budget", PASS, described)


def check_voice_config():
    """Phase 6 configuration, read-only (no model call, no `ModelUsage`):

    - `voice_providers`: STT and TTS rows exist (else `fail`: run
      `seed_ai_defaults`) and at least one of each is eligible for the
      gateway (else `warn`: voice is not configured here). An eligible,
      priced (cloud) speech row is named -- the owner's d2 toggle is on.
    - `voice_routes`: every `VOICE_TTS_ROUTES` provider names an eligible
      TTS row; a missing (e.g. no `VOICE_API_KEY`, so no local rows, or a
      typo) or ineligible routed row is a `warn` -- the gateway falls back
      to priority order (A2), the owner sees which route is not honoured.
    - `voice_budget`: an active `AIBudget` covers every audio purpose
      (`fail` otherwise -- A3); a cover without a monthly cap is a `warn`.
    """
    from ai_core.models import ModelProvider

    rows = {
        capability: list(
            ModelProvider.objects.filter(capability=capability).order_by("priority", "name")
        )
        for capability in AUDIO_CAPABILITIES
    }
    return [
        _check_voice_providers(rows),
        _check_voice_routes({p.name: p for p in rows["tts"]}),
        _check_voice_budget(),
    ]


def check_voice_mute():
    """A `?mute=1` voice tab is silent (brief A7): no mic opened, no speech
    request, nothing played. That is browser behaviour, so the check runs a
    real browser -- `PREFLIGHT_VOICE_MUTE_CMD`, normally the Playwright spec
    `frontend/e2e/voice-mute.e2e.ts` against the running BFF -- and passes
    on exit 0. Unset: a warning (not configured here). The command's output
    is never shown (it may carry URLs or cookies)."""
    command = getattr(settings, "PREFLIGHT_VOICE_MUTE_CMD", "")
    try:
        argv = shlex.split(command or "")
    except ValueError:
        return [CheckResult("voice_mute", FAIL, "PREFLIGHT_VOICE_MUTE_CMD does not parse")]
    if not argv:
        return [CheckResult("voice_mute", WARN, "PREFLIGHT_VOICE_MUTE_CMD not set")]
    timeout = getattr(settings, "PREFLIGHT_VOICE_MUTE_TIMEOUT_SECONDS", 180)
    try:
        code = run_command(argv, timeout=timeout)
    except Exception as exc:
        if type(exc).__name__ == "TimeoutExpired":
            return [CheckResult("voice_mute", FAIL, f"browser run timed out after {timeout}s")]
        return [CheckResult("voice_mute", FAIL, f"browser run failed: {_error_label(exc)}")]
    if code != 0:
        return [CheckResult("voice_mute", FAIL, f"browser run exited {code}: muted tab not silent")]
    return [CheckResult("voice_mute", PASS, "muted tab opened no mic and played nothing")]


# --- Phase 8A: learning memory (closeout) ---------------------------------

# Which beat runs each learning task (config/celery.py): the git producer
# clones with the runner's credentials, so it is on the runner beat.
LEARNING_BEAT_TASKS = {
    "worker": (
        "memory.ingestion.tasks.run_learning_night_task",
        "memory.ingestion.tasks.recover_stale_episode_extraction_jobs",
        "memory.ingestion.tasks.run_weekly_reflection_task",
        "memory.ingestion.tasks.purge_expired_episodes_task",
        "connectors.tasks.ingest_calendar_metadata",
        "assistant.tasks.assemble_self_block_task",
        "tools.tasks.pull_transcripts_task",
    ),
    "runner": ("harness.tasks.ingest_repo_commits",),
}
# The corpus files and the marker that keeps provider prefixes split in the
# committed files (see memory/tests/test_scrubber.py).
SCRUBBER_CORPUS_FILES = {"secrets": "secrets.jsonl", "pii": "pii.jsonl", "benign": "benign.jsonl"}
SCRUBBER_TRANSCRIPT_CORPUS = "transcripts.jsonl"
_SCRUBBER_SPLIT_MARKER = "<<>>"


def _installation_owner_workspace():
    from django.contrib.auth import get_user_model

    from workspaces.models import Workspace

    owner = (
        get_user_model()
        .objects.filter(is_owner=True, is_agent=False, is_active=True)
        .order_by("created_at")
        .first()
    )
    if owner is None:
        return None
    return (
        Workspace.objects.filter(owner=owner, type=Workspace.Type.PERSONAL)
        .order_by("created_at")
        .first()
    )


def _check_learning_sources():
    from memory.ingestion.mapping import rule_source_for
    from memory.models import IngestionSource, WorkspaceMappingRule

    workspace = _installation_owner_workspace()
    if workspace is None:
        return CheckResult("learning_sources", WARN, "no installation owner workspace")
    sources = {s.kind: s for s in IngestionSource.objects.filter(workspace=workspace)}
    missing = [kind for kind in IngestionSource.Kind.values if kind not in sources]
    if missing:
        return CheckResult(
            "learning_sources",
            FAIL,
            f"no {', '.join(missing)} source: run seed_ingestion_sources",
        )
    enabled = [s for s in sources.values() if s.enabled]
    if not enabled:
        return CheckResult("learning_sources", WARN, "all four sources are disabled")
    # A shared-rules source (Phase 8B branch 8: the transcript sources) is
    # mapped by the hooks source's rules.
    rule_sources = {s.id: rule_source_for(s) for s in enabled}
    mapped = set(
        WorkspaceMappingRule.objects.filter(
            source_id__in=[r.id for r in rule_sources.values() if r is not None], enabled=True
        ).values_list("source_id", flat=True)
    )
    problems = []
    for source in sorted(enabled, key=lambda s: s.kind):
        if source.forgotten_at is not None:
            problems.append(f"{source.kind} forgotten")
        elif source.paused_at is not None:
            problems.append(f"{source.kind} paused")
        rule_source = rule_sources[source.id]
        if rule_source is None or rule_source.id not in mapped:
            problems.append(f"{source.kind} has no mapping rule (every item is dropped)")
    git = sources[IngestionSource.Kind.GIT]
    if git.enabled and not getattr(settings, "MEMORY_OWNER_GIT_EMAILS", []):
        problems.append("MEMORY_OWNER_GIT_EMAILS is empty (no commit counts as owner text)")
    detail = "enabled: " + ", ".join(sorted(s.kind for s in enabled))
    if problems:
        return CheckResult("learning_sources", WARN, f"{detail}; " + "; ".join(problems))
    return CheckResult("learning_sources", PASS, detail)


def _check_learning_beat():
    schedules, registered = beat_schedules()
    if not schedules:
        return CheckResult("learning_beat", FAIL, "no beat schedule published by the Celery app")
    problems = []
    for mode, tasks in LEARNING_BEAT_TASKS.items():
        scheduled = {entry["task"] for entry in (schedules.get(mode) or {}).values()}
        for task in tasks:
            if task not in scheduled:
                problems.append(f"{task} not on the {mode} beat")
            if task not in registered:
                problems.append(f"{task} not registered")
    if problems:
        return CheckResult("learning_beat", FAIL, "; ".join(problems))
    return CheckResult(
        "learning_beat",
        PASS,
        f"{len(LEARNING_BEAT_TASKS['worker'])} worker-beat and "
        f"{len(LEARNING_BEAT_TASKS['runner'])} runner-beat learning tasks scheduled",
    )


def scrubber_corpus_dir():
    configured = getattr(settings, "PREFLIGHT_SCRUBBER_CORPUS_DIR", "")
    if configured:
        return Path(configured)
    import memory

    return Path(memory.__file__).resolve().parent / "tests" / "scrubber_corpus"


def _corpus_rows(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows.append(
                {
                    "id": str(row["id"]),
                    "class": row.get("class"),
                    "text": row["text"].replace(_SCRUBBER_SPLIT_MARKER, ""),
                    "needles": [
                        n.replace(_SCRUBBER_SPLIT_MARKER, "") for n in row.get("needles", [])
                    ],
                }
            )
    return rows


def _load_scrubber_corpus(directory):
    corpus = {}
    for name, filename in SCRUBBER_CORPUS_FILES.items():
        corpus[name] = _corpus_rows(directory / filename)
    # Phase 8B branch 8 (D14): the labelled transcript fragments join the
    # secrets and PII classes (the same 100% secrets-recall gate).
    transcripts = directory / SCRUBBER_TRANSCRIPT_CORPUS
    if transcripts.is_file():
        for row in _corpus_rows(transcripts):
            if row["class"] == "secret":
                corpus["secrets"].append(row)
            elif row["class"] == "pii":
                corpus["pii"].append(row)
    return corpus


def _check_learning_scrubber():
    """The scrubber self-test (ADR-012 decision 3) on this deployment's
    code: every corpus secret must be gone (else `fail` -- a release-gate
    regression), every PII needle too (else `warn`), benign text unchanged
    (else `warn`). Only corpus ids are reported, never text."""
    from memory.ingestion.scrubber import scrub_text

    directory = scrubber_corpus_dir()
    if not all((directory / f).is_file() for f in SCRUBBER_CORPUS_FILES.values()):
        return CheckResult("learning_scrubber", WARN, "scrubber corpus not found here")
    try:
        corpus = _load_scrubber_corpus(directory)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        return CheckResult("learning_scrubber", FAIL, f"corpus unreadable: {_error_label(exc)}")

    def missed(rows):
        return [
            row["id"]
            for row in rows
            if any(needle in scrub_text(row["text"]).text for needle in row["needles"])
        ]

    if not corpus["secrets"]:
        return CheckResult("learning_scrubber", FAIL, "the secrets corpus is empty")
    secrets_missed = missed(corpus["secrets"])
    pii_missed = missed(corpus["pii"])
    benign_changed = [
        row["id"] for row in corpus["benign"] if scrub_text(row["text"]).text != row["text"]
    ]
    secrets_total, pii_total = len(corpus["secrets"]), len(corpus["pii"])
    benign_total = len(corpus["benign"])
    detail = (
        f"secrets {secrets_total - len(secrets_missed)}/{secrets_total}, "
        f"PII {pii_total - len(pii_missed)}/{pii_total}, "
        f"benign {benign_total - len(benign_changed)}/{benign_total} unchanged"
    )
    if secrets_missed:
        return CheckResult(
            "learning_scrubber", FAIL, f"{detail}; missed: {', '.join(secrets_missed)}"
        )
    if pii_missed or benign_changed:
        flagged = ", ".join(pii_missed + benign_changed)
        return CheckResult("learning_scrubber", WARN, f"{detail}; check: {flagged}")
    return CheckResult("learning_scrubber", PASS, detail)


def _check_learning_budget():
    from ai_core.budgets import budgets_for

    purposes = tuple(getattr(settings, "AI_LEARNING_PURPOSES", ()) or ())
    if not purposes:
        return CheckResult("learning_budget", FAIL, "AI_LEARNING_PURPOSES is empty")
    covers = {p: budgets_for(p) for p in purposes}
    uncovered = [p for p in purposes if not covers[p]]
    if uncovered:
        return CheckResult(
            "learning_budget",
            FAIL,
            f"no active budget covers {', '.join(uncovered)}: run seed_ai_defaults",
        )
    budgets = {b.pk: b for found in covers.values() for b in found}
    described = "; ".join(
        _describe_budget(b) for b in sorted(budgets.values(), key=lambda b: b.name)
    )
    uncapped = [p for p in purposes if all(b.monthly_limit_usd is None for b in covers[p])]
    if uncapped:
        return CheckResult(
            "learning_budget", WARN, f"no monthly cap on {', '.join(uncapped)}: {described}"
        )
    return CheckResult("learning_budget", PASS, described)


def _check_learning_last_run():
    """The latest night's episode-extraction jobs: a terminal `failed` job is
    a `fail` (its episodes were never learned); deferred (budget), pending,
    lease-expired running or not-yet-consolidated jobs are a `warn`."""
    from django.utils import timezone

    from memory.models import EpisodeExtractionJob

    status = EpisodeExtractionJob.Status
    latest = EpisodeExtractionJob.objects.order_by("-night", "-created_at").first()
    if latest is None:
        return CheckResult("learning_last_run", WARN, "no extraction run yet")
    jobs = list(EpisodeExtractionJob.objects.filter(night=latest.night))
    counts = Counter(job.status for job in jobs)
    detail = f"night {latest.night.isoformat()}: " + ", ".join(
        f"{counts[s]} {s}" for s in status.values if counts[s]
    )
    if counts[status.FAILED]:
        errors = sorted({j.error_type or "error" for j in jobs if j.status == status.FAILED})
        return CheckResult("learning_last_run", FAIL, f"{detail} ({', '.join(errors)})")
    now = timezone.now()
    stuck = [
        j
        for j in jobs
        if j.status == status.RUNNING and j.lease_expires_at and j.lease_expires_at < now
    ]
    waiting = [j for j in jobs if j.status == status.SUCCEEDED and j.consolidated_at is None]
    notes = []
    if stuck:
        notes.append(f"{len(stuck)} with an expired lease")
    if counts[status.DEFERRED]:
        notes.append("deferred by a budget")
    if counts[status.PENDING]:
        notes.append("pending retry")
    if waiting:
        notes.append(f"{len(waiting)} awaiting consolidation")
    if notes:
        return CheckResult("learning_last_run", WARN, f"{detail}; " + "; ".join(notes))
    return CheckResult("learning_last_run", PASS, detail)


def check_learning():
    """Phase 8A configuration and health, read-only (no model call, no
    `ModelUsage`): `learning_sources`, `learning_beat`, `learning_scrubber`,
    `learning_budget`, `learning_last_run`."""
    return [
        _check_learning_sources(),
        _check_learning_beat(),
        _check_learning_scrubber(),
        _check_learning_budget(),
        _check_learning_last_run(),
    ]


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
    ("voice_config", check_voice_config),
    ("voice_mute", check_voice_mute),
    ("learning", check_learning),
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
