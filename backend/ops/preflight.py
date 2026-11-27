"""`manage.py preflight` (Phase 5 closeout; MASTER_PLAN "Roadmap update
2026-11-20"): live end-to-end checks against the RUNNING stack, not mocks.

Each check returns one or more `CheckResult`s with a status:

- `pass` -- the service answered as expected;
- `fail` -- it is configured but broken (the command exits non-zero);
- `warn` -- it is not configured here (or deliberately skipped), so
  nothing could be checked. A warning never fails the run.

Every outside contact goes through a module-level seam (`http_get`,
`db_fetchone`, `redis_ping`, `celery_ping`, `worker_signing_key_id`,
`served_spki_sha256`, `node_conformance_drift`, `gateway_factory`,
`pin_report`, `run_command`, `node_conformance_case_counts`,
`escape_suite_report`, `approve_request_source`, `drf_routes`,
`friday_voice_dir`) so the unit tests replace exactly that and
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

Phase 9 closeout adds the node checks (`check_nodes`, read-only, no model
call, nothing sent to a node): `node_gateway` (the node-gateway's `/healthz`
answers and its Redis is up), `node_signing_key` (a usable key that is not a
published test or conformance key, the key every active node pinned at
pairing, and the same key id in web, worker -- the `nodes.signing_key_id`
task through the seam `worker_signing_key_id` -- and node-gateway),
`node_link` (per active node: presence, heartbeat age, catalogue, counts; a
`warn` when none is paired, because the Mac session is deferred),
`node_tls_pin` (the served certificate's SPKI against `NODE_TLS_PIN_SHA256`,
seam `served_spki_sha256`), `node_audit_chain` (`verify_node_audit_chain`)
and `node_conformance` (`build_node_conformance --check`, seam
`node_conformance_drift`).

Phase 10 closeout adds the control checks (`check_control`, read-only, no
model call, nothing sent to a node): `node_grant_config` (grant TTL 70..120 s,
dispatch delay <= 60 s, the 600 s `node_action` approval TTL, the approval and
node action sweeps on the worker beat), `node_protocol_v2` (per active node:
negotiated version and invocable gate/confirm counts; a `warn` when no node
speaks v2, because the Mac session is deferred), `node_conformance_v2` (the
v1 and v2 case files and `vectors/sandbox.json` current, seams
`node_conformance_drift` and `node_conformance_case_counts`),
`node_escape_suite` (`node_escape_suite --executor fake`, seam
`escape_suite_report`; the ADR-014 Mac evidence date, a `warn` while pending),
`node_output_scrubber` (the gateway's /healthz reports its scrubber),
`trust_check` (the real check registered, never the branch-3 stub; tier,
budget and prompt of `control.trust_check`), `node_action_actor` (the owner
hooks declared; `approve_request` calls the approver hook, seam
`approve_request_source`) and `harness_drivers` (per driver: disabled, or
enabled with d4, a non-provisional snapshot and agreeing pins).

Phase 11 closeout adds the live-voice checks (`check_live_voice`, read-only,
no model call, nothing sent to a node, no `voice` import -- `voice` is a
leaf whose contract lists `ops`): `voice_live_channel` (the `node_voice`
kind and its authenticator reach exactly the D4 views, a walk of the live
URL table through the seam `drf_routes`; token TTL <= 1 h),
`voice_live_policy` (D11 through `accounts.voice_live.model_verdicts`, the
policy endpoint's own function: `warn` while no model's latest pair passes,
`fail` when always-on is enabled without one), `voice_live_storm` (an
engaged `VoiceLiveMute` is a `warn` with its age), `voice_live_selftest`
(the nightly result line at `VOICE_LIVE_SELFTEST_RESULT_PATH`),
`friday_voice_conformance` (the `node/friday-voice` case, vector and schema
files against `conformance.lock`, seam `friday_voice_dir`; a `warn` where
no checkout exists) and `node_voice_state` (per active node, its last
`voice.state`; a `warn` while none reports, the Mac session is deferred).

Phase 12 closeout adds the reach checks, read-only (no model call, no run
started, no tool called, nothing stored): `check_proactive` --
`proactive_beat` (the heartbeat every <= 60 s and the notice prune on the
worker beat), `proactive_schedules` (every enabled schedule has a
`next_run_at` that is not stale; a `warn` while none is enabled, owner
step), `proactive_last_run` (no schedule's last run `failed`),
`proactive_digest` (tier, budget and prompt of `proactive.digest`) and
`proactive_probes` (none slow or failing on the last runs); `check_n8n` --
`n8n_allowlist` (`N8N_RELAY_ALLOWED_TOOLS`, the relay's
`BRIDGE_ALLOWED_TOOLS`, equals the manifest, seam
`n8n_relay_allowed_tools_setting`), `n8n_binding`, `n8n_relay` and
`n8n_pins` (a read-only `tools/list` through the relay, seam `pin_report`;
all three `warn` while no binding is enabled, owner step); `check_skills`
-- `skills_ai` (the three skills purposes' tiers, budgets and prompts),
`skills_beat` (curator and distiller scheduled) and `skills_parser` (every
usable skill's current version passes the current SKILL.md parser).

Phase 13 branch 7 adds the home-server checks (`check_home`, read-only, no
model call, nothing sent to a node, no backup started or read): `home_lan_only`
(every `DJANGO_ALLOWED_HOSTS` entry and the `PREFLIGHT_NODE_TLS_URL` host
resolve only to loopback, RFC 1918, ULA or Tailscale CGNAT addresses, seam
`resolve_host`), `home_tls_pin` (`node_tls_pin`'s comparison, seam
`served_spki_sha256`), `home_backup_fresh` and `home_restore_drill_age` (the
`status.json` / `drill.json` files `deploy/home/friday-backup.sh` and
`friday-restore-drill.sh` write on the host) and `home_disk_encryption` (the
owner's `HOME_FDE_CONFIRMED_ON` attestation).

Phase 13 closeout adds `check_home_control` (read-only, no model call,
nothing sent to a node): `nodes_labels` (unique node names per owner, rooms
set in a multi-node home), `ha_entities` (every allowlisted Home Assistant
entity inside the bridge's code maximum and still present in HA -- one
read-only `ha_states` read, seam `ha_state_ids`), `linux_node_vendor`
(`node/friday-node-linux/vendor.lock` against this backend, seam
`linux_node_dir`) and `node_protocol_frozen` (the node protocol files and
the `friday-voice` conformance lock hash to the Phase 12 closeout values:
Phase 13 adds no protocol surface).

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


def worker_signing_key_id(*, timeout):
    """The node signing key id a running worker reports (the
    `nodes.signing_key_id` task: the id only, never key material), or None
    when that worker has no usable key. A broker or result-backend failure,
    or no worker answering in time, propagates (the caller maps it)."""
    from celery import current_app

    return current_app.send_task("nodes.signing_key_id").get(timeout=timeout)


def served_spki_sha256(host, port, *, timeout):
    """sha256 of the SubjectPublicKeyInfo of the certificate `host:port`
    serves (SNI = host). Trust is not checked here: the question is only
    whether the served key is the pinned one (an overlay may serve a private
    CA's certificate). Network and TLS errors propagate."""
    import hashlib
    import socket
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # noqa: S501 -- pin comparison only, nothing is sent
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    spki = (
        x509.load_der_x509_certificate(der)
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    return hashlib.sha256(spki).digest()


def node_conformance_drift():
    """The stale conformance files (`build_node_conformance --check`): an
    empty list when every committed case and vector file and the vendored
    node scrubber are current."""
    from nodes.conformance import build
    from nodes.reference import vendor_scrubber

    drift = list(build.check())
    if vendor_scrubber.main(["--check"]) != 0:
        drift.append("reference/_scrubber_vendored.py")
    return drift


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
    # Phase 8B closeout: the Graphify lease-recovery scan rides the runner
    # beat with the git producer that triggers the runs.
    "runner": (
        "harness.tasks.ingest_repo_commits",
        "harness.tasks.recover_stale_graphify_runs",
    ),
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
    # The closeout makes the file required: a deployment without it fails
    # the gate (`learning_scrubber`), it is never silently skipped.
    corpus["transcripts"] = _corpus_rows(directory / SCRUBBER_TRANSCRIPT_CORPUS)
    for row in corpus["transcripts"]:
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
    if not (directory / SCRUBBER_TRANSCRIPT_CORPUS).is_file():
        return CheckResult(
            "learning_scrubber",
            FAIL,
            f"{SCRUBBER_TRANSCRIPT_CORPUS} is missing (Phase 8B D14: the transcript "
            "fragments are part of the 100% secrets-recall gate)",
        )
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
    transcript_secrets = [r for r in corpus["transcripts"] if r["class"] == "secret"]
    if not transcript_secrets:
        return CheckResult(
            "learning_scrubber", FAIL, f"{SCRUBBER_TRANSCRIPT_CORPUS} has no secret item"
        )
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
        f"benign {benign_total - len(benign_changed)}/{benign_total} unchanged, "
        f"incl. {len(corpus['transcripts'])} transcript fragments"
    )
    if secrets_missed:
        return CheckResult(
            "learning_scrubber", FAIL, f"{detail}; missed: {', '.join(secrets_missed)}"
        )
    if pii_missed or benign_changed:
        flagged = ", ".join(pii_missed + benign_changed)
        return CheckResult("learning_scrubber", WARN, f"{detail}; check: {flagged}")
    return CheckResult("learning_scrubber", PASS, detail)


# Phase 8B closeout: the purposes whose budgets `learning_budget` must find
# beyond the nightly two (D15): fact reconciliation, the weekly reflection
# and the eval-set answers (`learning_eval`, outside the nightly cap).
LEARNING_BUDGET_REQUIRED = (
    "memory.fact_reconciliation",
    "memory.reflection",
    "memory.eval_answer",
)


def _check_learning_budget():
    from ai_core.budgets import budgets_for

    learning = tuple(getattr(settings, "AI_LEARNING_PURPOSES", ()) or ())
    if not learning:
        return CheckResult("learning_budget", FAIL, "AI_LEARNING_PURPOSES is empty")
    configured = learning + tuple(getattr(settings, "AI_LEARNING_EVAL_PURPOSES", ()) or ())
    not_configured = [p for p in LEARNING_BUDGET_REQUIRED if p not in configured]
    if not_configured:
        return CheckResult(
            "learning_budget",
            FAIL,
            f"{', '.join(not_configured)} not in AI_LEARNING_PURPOSES / AI_LEARNING_EVAL_PURPOSES",
        )
    purposes = tuple(dict.fromkeys(configured))
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


# --- Phase 8B: transcripts and knowledge graph (closeout) -----------------

# The tsvector GIN indexes of hybrid retrieval (D8; migrations
# memory/0015_hybrid_retrieval and knowledge/0004_chunk_search).
RETRIEVAL_GIN_INDEXES = (
    "memory_memory_search_gin",
    "memory_memoryfact_search_gin",
    "knowledge_knowledgechunk_search_gin",
)
# The label `harness/docker/graphify.Dockerfile` stamps on the image; its
# value must equal the `graphifyy` pin of `graphify.lock.txt`.
GRAPHIFY_VERSION_LABEL = "org.friday.graphify.version"
_GRAPHIFY_PIN = re.compile(r"^graphifyy==([0-9][0-9A-Za-z.\-]*)", re.MULTILINE)


def existing_index_names(names):
    """The subset of `names` that exist as indexes (pg_indexes)."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE indexname = ANY(%s)", [list(names)])
        return {row[0] for row in cursor.fetchall()}


def docker_image_labels(image, *, timeout):
    """The labels of a local Docker image: a dict, or None when the image is
    not present. `OSError` (no `docker` binary here) and `TimeoutExpired`
    propagate. Only the label map is read; no container starts."""
    import subprocess

    completed = subprocess.run(  # noqa: S603 -- fixed argv, image from settings, no shell
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],  # noqa: S607
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        labels = json.loads(completed.stdout or b"null")
    except ValueError:
        return {}
    return labels if isinstance(labels, dict) else {}


def graphify_pinned_version():
    """The `graphifyy==` pin of the hash-locked requirements the image is
    built from (one source of truth with the Dockerfile's label)."""
    import harness

    lock = Path(harness.__file__).resolve().parent / "docker" / "graphify.lock.txt"
    match = _GRAPHIFY_PIN.search(lock.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def transcript_node_port():
    """The transcript pull's installed node port (`tools.transcript_ingest`)."""
    from tools.transcript_ingest import get_node_port

    return get_node_port()


def _check_graph_integrity():
    """The temporal graph's invariants (D2) over every row, trashed ones
    included: no fact crosses workspaces (subject, object and fact share one
    workspace), every approved fact with `superseded_by` set has a closed
    window, no window ends before it starts. Counts only, never a
    statement."""
    from django.db.models import F, Q

    from memory.models import MemoryEntity, MemoryFact

    facts = MemoryFact.all_objects
    crossing = facts.filter(
        ~Q(subject__workspace_id=F("workspace_id"))
        | (Q(object_entity__isnull=False) & ~Q(object_entity__workspace_id=F("workspace_id")))
    ).count()
    open_superseded = facts.filter(
        status=MemoryFact.Status.APPROVED, superseded_by__isnull=False, valid_to__isnull=True
    ).count()
    inverted = facts.filter(valid_to__isnull=False, valid_to__lt=F("valid_from")).count()
    problems = [
        f"{count} {label}"
        for count, label in (
            (crossing, "fact(s) crossing workspaces"),
            (open_superseded, "superseded approved fact(s) with no valid_to"),
            (inverted, "fact(s) with valid_to before valid_from"),
        )
        if count
    ]
    if problems:
        return CheckResult("graph_integrity", FAIL, "; ".join(problems))
    return CheckResult(
        "graph_integrity",
        PASS,
        f"{facts.count()} fact(s), {MemoryEntity.all_objects.count()} entit(ies) consistent",
    )


def _hybrid_compare_recorded():
    from memory.models import MemoryEvalRun

    return MemoryEvalRun.objects.filter(
        set=MemoryEvalRun.Set.PRIVATE,
        kind=MemoryEvalRun.Kind.COMPARE,
        retrieval_mode="hybrid",
        compression_purpose="",
        verdict=MemoryEvalRun.Verdict.PASS,
    ).exists()


def _check_retrieval_indexes():
    """`build_vector_indexes --check` (an HNSW index per active embedding
    provider and table), the three tsvector GIN indexes, and the retrieval
    mode. `hybrid` without a recorded passing private compare is a `warn`
    (D8: the default flips only on the owner's recorded compare)."""
    from memory.retrieval.indexes import (
        MISSING,
        SKIPPED_DIMENSION,
        SKIPPED_NO_DIMENSION,
        STALE,
        index_status,
    )

    name = "retrieval_indexes"
    mode = getattr(settings, "MEMORY_RETRIEVAL_MODE", "legacy")
    entries = index_status()
    gin_missing = sorted(set(RETRIEVAL_GIN_INDEXES) - existing_index_names(RETRIEVAL_GIN_INDEXES))
    failing = sorted(e.name for e in entries if e.status in (MISSING, STALE))
    skipped = sorted(
        {e.provider_name for e in entries if e.status in (SKIPPED_DIMENSION, SKIPPED_NO_DIMENSION)}
    )
    providers = {e.provider_id for e in entries}
    healthy = sum(1 for e in entries if e.status not in (MISSING, STALE))
    detail = (
        f"mode {mode}; {healthy}/{len(entries)} HNSW entr(ies) over {len(providers)} "
        f"provider(s); {len(RETRIEVAL_GIN_INDEXES) - len(gin_missing)}"
        f"/{len(RETRIEVAL_GIN_INDEXES)} GIN"
    )
    problems = []
    if failing:
        problems.append(f"missing or stale: {', '.join(failing)} (run build_vector_indexes)")
    if gin_missing:
        problems.append(f"GIN missing: {', '.join(gin_missing)} (run migrate)")
    if problems:
        return CheckResult(name, FAIL, f"{detail}; " + "; ".join(problems))
    notes = []
    if not entries:
        notes.append("no active embedding provider (retrieval is full-text only)")
    if skipped:
        notes.append(f"no HNSW for {', '.join(skipped)} (exact scan)")
    if mode == "hybrid" and not _hybrid_compare_recorded():
        notes.append("hybrid without a recorded passing private compare (D8)")
    if notes:
        return CheckResult(name, WARN, f"{detail}; " + "; ".join(notes))
    return CheckResult(name, PASS, detail)


def _graphify_image_problem(enabled, notes):
    """None when the pinned image is present with the right label; a `fail`
    reason when it is mislabelled, or absent while a repo needs it. Without
    a `docker` binary here (the web and worker containers) it is a note
    only: the image lives on the runner host."""
    image = getattr(settings, "HARNESS_IMAGE_GRAPHIFY", "")
    pinned = graphify_pinned_version()
    if not image or pinned is None:
        return "HARNESS_IMAGE_GRAPHIFY or the graphifyy pin is not set"
    try:
        labels = docker_image_labels(image, timeout=_timeout())
    except Exception as exc:
        notes.append(f"image not checked here ({_error_label(exc)}; run on the runner host)")
        return None
    if labels is None:
        message = f"image {image} not built (owner step: docker build graphify.Dockerfile)"
        if enabled:
            return message
        notes.append(message)
        return None
    found = labels.get(GRAPHIFY_VERSION_LABEL)
    if found != pinned:
        return f"image {image} label {GRAPHIFY_VERSION_LABEL}={found or 'none'}, pin {pinned}"
    return None


def _check_graphify():
    """The Graphify job (D10): the pinned image is present locally with its
    version label, and the last run of each `graphify_enabled` repo
    succeeded. A `warn` when no repo is enabled or none ran yet; a `fail`
    on a failed last run or a missing/mislabelled image a repo needs."""
    from harness.models import GraphifyRun, HarnessRepo

    name = "graphify"
    repos = list(
        HarnessRepo.objects.filter(graphify_enabled=True, enabled=True).order_by("created_at")
    )
    notes = []
    problems = []
    image_problem = _graphify_image_problem(bool(repos), notes)
    if image_problem:
        problems.append(image_problem)
    if not repos:
        notes.insert(0, "no repo has graphify_enabled (owner step)")
    statuses = Counter()
    for repo in repos:
        last = GraphifyRun.objects.filter(repo=repo).order_by("-created_at").first()
        if last is None:
            statuses["never ran"] += 1
            continue
        statuses[last.status] += 1
        if last.status == GraphifyRun.Status.FAILED:
            problems.append(f"{repo.name}: last run failed ({last.error_type or 'error'})")
    detail = f"{len(repos)} enabled repo(s)"
    if statuses:
        counted = sorted(statuses.items())
        detail += ": " + ", ".join(f"{count} {status}" for status, count in counted)
    if problems:
        return CheckResult(name, FAIL, f"{detail}; " + "; ".join(problems))
    if statuses["never ran"]:
        notes.append("an enabled repo has no run yet")
    in_flight = statuses[GraphifyRun.Status.PENDING] + statuses[GraphifyRun.Status.RUNNING]
    if in_flight:
        notes.append(f"{in_flight} in flight")
    if notes:
        return CheckResult(name, WARN, f"{detail}; " + "; ".join(notes))
    return CheckResult(name, PASS, f"{detail}; image label matches the pin")


def _compressor_health_url():
    parsed = urlparse(getattr(settings, "AI_COMPRESSION_URL", "") or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/healthz"


def _compression_eval_recorded(purpose):
    from memory.models import MemoryEvalRun

    return MemoryEvalRun.objects.filter(
        set=MemoryEvalRun.Set.PRIVATE,
        kind=MemoryEvalRun.Kind.COMPARE,
        compression_purpose=purpose,
        verdict=MemoryEvalRun.Verdict.PASS,
    ).exists()


def _check_compressor():
    """The Headroom stage (D11). Off: `pass (off)`. On: the sidecar's
    `/healthz` answers with telemetry off and offline, and every enabled
    purpose has a recorded passing private compression compare (the
    go/no-go: <= 1 point loss, zero extra leaks)."""
    from ai_core.compression import COMPRESSION_BYPASS

    name = "compressor"
    if not getattr(settings, "AI_COMPRESSION_ENABLED", False):
        return CheckResult(name, PASS, "off (AI_COMPRESSION_ENABLED is false)")
    purposes = sorted(getattr(settings, "AI_COMPRESSION_PURPOSES", ()) or ())
    url = _compressor_health_url()
    if not url:
        return CheckResult(name, FAIL, "AI_COMPRESSION_URL is not an http(s) URL")
    try:
        status, text = _get_page(url)
    except Exception as exc:
        return CheckResult(name, FAIL, f"sidecar unreachable: {_error_label(exc)}")
    if status != 200:
        return CheckResult(name, FAIL, f"sidecar /healthz HTTP {status}")
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("status") != "ok":
        return CheckResult(name, FAIL, "unexpected sidecar /healthz body")
    problems = []
    if body.get("telemetry") != "off" or body.get("offline") is not True:
        problems.append("the sidecar reports telemetry on or not offline")
    unrecorded = [
        p for p in purposes if p not in COMPRESSION_BYPASS and not _compression_eval_recorded(p)
    ]
    if unrecorded:
        problems.append(
            f"no recorded passing private compare for {', '.join(unrecorded)} "
            "(memory_eval compare --compression)"
        )
    detail = f"sidecar healthy; purposes: {', '.join(purposes) or 'none'}"
    if problems:
        return CheckResult(name, FAIL, f"{detail}; " + "; ".join(problems))
    bypassed = [p for p in purposes if p in COMPRESSION_BYPASS]
    if not purposes:
        return CheckResult(name, WARN, f"{detail}; no purpose enabled (nothing is compressed)")
    if bypassed:
        return CheckResult(name, WARN, f"{detail}; {', '.join(bypassed)} always bypassed")
    return CheckResult(name, PASS, detail)


def _last_pull(capability):
    from audit.models import AuditLog
    from memory.ingestion.transcript_audit import PULL_AUDIT_ACTION

    return (
        AuditLog.objects.filter(action=PULL_AUDIT_ACTION, metadata__capability=capability)
        .order_by("-created_at")
        .values_list("metadata", flat=True)
        .first()
    )


def _transcript_capability_result(port, node, source, rows, problems, notes):
    from memory.ingestion.transcript_audit import CAPABILITY_BY_SOURCE_KIND

    capability = CAPABILITY_BY_SOURCE_KIND[source.kind]
    if source.paused_at is not None:
        notes.append(f"{source.kind} paused")
    if not port.capability_granted(node, capability):
        notes.append(f"{capability} not granted (Touch ID grant, Mac session)")
        return
    last = _last_pull(capability)
    if not isinstance(last, dict):
        notes.append(f"{capability}: no pull yet")
        return
    status = str(last.get("status") or "unknown")[:40]
    code = str(last.get("code") or "")[:40]
    rows.append(f"{capability} {status}{f' ({code})' if code else ''}")
    if status in ("failed", "server_refused"):
        problems.append(f"{capability} last pull {status}")
    elif status != "ok":
        notes.append(f"{capability} last pull {status}")


def _check_transcripts():
    """The transcript pull (D13). Both sources disabled: `pass (off)`. An
    enabled source: the owner's node is reachable through the pull's port,
    the capability is granted, and the last pull's status (`ok` passes,
    `node_refused` warns, `failed`/`server_refused` fail). No port, no node
    or no grant is a `warn`: the `nodes`-backed port is installed at app
    start (runbook A12), so "no node port" means the install did not run;
    no reachable node or no grant waits on the Swift owner-turn readers
    and the Touch ID grant (runbook B4, Mac session M1).
    Codes and counts only, never a path or a turn."""
    from memory.ingestion.transcript_audit import CAPABILITY_BY_SOURCE_KIND
    from memory.models import IngestionSource
    from tools.transcript_ingest import UnavailableNodePort

    name = "transcripts"
    workspace = _installation_owner_workspace()
    if workspace is None:
        return CheckResult(name, WARN, "no installation owner workspace")
    enabled = list(
        IngestionSource.objects.filter(
            workspace=workspace, kind__in=list(CAPABILITY_BY_SOURCE_KIND), enabled=True
        ).order_by("kind")
    )
    if not enabled:
        return CheckResult(name, PASS, "off (both transcript sources disabled)")
    port = transcript_node_port()
    if isinstance(port, UnavailableNodePort):
        return CheckResult(
            name,
            WARN,
            "no node port installed (the nodes-backed port did not install; the pull is a no-op)",
        )
    node = port.owner_node(workspace.owner)
    if node is None:
        return CheckResult(
            name, WARN, "the owner's node is not reachable (Mac session pending owner evaluation)"
        )
    rows, problems, notes = [], [], []
    for source in enabled:
        _transcript_capability_result(port, node, source, rows, problems, notes)
    detail = "; ".join(rows) or "node reachable, no pull status"
    if problems:
        return CheckResult(name, FAIL, f"{detail}; " + "; ".join(problems))
    if notes:
        return CheckResult(name, WARN, f"{detail}; " + "; ".join(notes))
    return CheckResult(name, PASS, detail)


def _metric(value):
    return "n/a" if value is None else f"{value:.4f}"


def _check_eval_baseline():
    """A private baseline `MemoryEvalRun` exists (D9, M2 item 1). Without
    one it is a `warn` (pending owner evaluation), never a failure. Metrics
    only: the row stores nothing else."""
    from memory.models import MemoryEvalRun

    name = "eval_baseline"
    baselines = MemoryEvalRun.objects.filter(kind=MemoryEvalRun.Kind.BASELINE)
    private = baselines.filter(set=MemoryEvalRun.Set.PRIVATE).order_by("-started_at").first()
    if private is None:
        public = baselines.filter(set=MemoryEvalRun.Set.PUBLIC).exists()
        return CheckResult(
            name,
            WARN,
            "no private baseline recorded (pending owner evaluation, M2 item 1)"
            + ("; a public baseline exists" if public else ""),
        )
    return CheckResult(
        name,
        PASS,
        f"private baseline {private.started_at.date().isoformat()} ({private.retrieval_mode}): "
        f"recall@10 {_metric(private.recall_at_10)}, leak rate {_metric(private.leak_rate)}, "
        f"temporal {_metric(private.temporal_correctness)}, {private.total} case(s)",
    )


def check_graph():
    """Phase 8B configuration and health, read-only (no model call, nothing
    sent to a node, no container started): `graph_integrity`,
    `retrieval_indexes`, `graphify`, `compressor`, `transcripts`,
    `eval_baseline`. One crashing check is its own `fail` row."""
    return [
        _guarded("graph_integrity", _check_graph_integrity),
        _guarded("retrieval_indexes", _check_retrieval_indexes),
        _guarded("graphify", _check_graphify),
        _guarded("compressor", _check_compressor),
        _guarded("transcripts", _check_transcripts),
        _guarded("eval_baseline", _check_eval_baseline),
    ]


# --- Phase 9: Friday Node (closeout) ----------------------------------------

# A connected node writes `last_heartbeat_at` at most once per 60 s
# (`nodes.presence`); a row older than this while the presence key says
# "connected" means the gateway stopped writing heartbeats.
NODE_HEARTBEAT_STALE_SECONDS = 180

_SIGNING_KEY_REASONS = {
    "missing": "NODE_SIGNING_KEY is not set in web",
    "invalid": "NODE_SIGNING_KEY in web is not a valid Ed25519 key",
    "test_key": "NODE_SIGNING_KEY in web is a published test or conformance key",
    "retired_invalid": "NODE_SIGNING_KEYS_RETIRED has a malformed entry",
}


def _node_gateway_health():
    """`(result, healthz)` for `node_gateway`: the result and the gateway's
    /healthz JSON (None when not configured or unusable)."""
    url = getattr(settings, "PREFLIGHT_NODE_GATEWAY_URL", "")
    if not url:
        return CheckResult("node_gateway", WARN, "PREFLIGHT_NODE_GATEWAY_URL not set"), None
    try:
        status, text = _get_page(url)
    except Exception as exc:
        return CheckResult("node_gateway", FAIL, f"unreachable: {_error_label(exc)}"), None
    if status != 200:
        return CheckResult("node_gateway", FAIL, f"HTTP {status}"), None
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("status") != "ok":
        return CheckResult("node_gateway", FAIL, "unexpected /healthz body"), None
    if body.get("redis") is not True:
        return CheckResult("node_gateway", FAIL, "the gateway reports Redis down"), body
    connected = body.get("connected_nodes")
    connected = connected if isinstance(connected, int) else 0
    return CheckResult("node_gateway", PASS, f"Redis OK, {connected} node(s) connected"), body


def _active_nodes():
    """Every paired node whose device is not revoked or expired, with its
    status. Preflight reads the whole installation (`ops` is operations
    tooling; nothing here reaches a view or an agent tool)."""
    from nodes.models import Node
    from nodes.selectors import node_status

    nodes = Node.objects.select_related("device", "device__user").order_by("created_at")
    return [(node, status) for node in nodes if (status := node_status(node)) != "revoked"]


def _gateway_key_problem(healthz, web_id, notes):
    if healthz is None:
        notes.append("node-gateway key id not compared (no /healthz)")
        return None
    gateway_id = healthz.get("signing_key_id")
    if not gateway_id:
        return "no usable key in node-gateway"
    if gateway_id != web_id:
        return "node-gateway signs with another key than web"
    return None


def _worker_key_problem(web_id, notes):
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        notes.append("worker key id not compared (CELERY_TASK_ALWAYS_EAGER)")
        return None
    try:
        worker_id = worker_signing_key_id(timeout=_timeout())
    except Exception as exc:
        return f"no worker answered nodes.signing_key_id ({_error_label(exc)})"
    if not worker_id:
        return "no usable key in the worker"
    if worker_id != web_id:
        return "the worker signs with another key than web"
    return None


def _check_node_signing_key(healthz, active):
    """A usable key in web (not a published test or conformance key), the
    same key id in node-gateway (/healthz) and a worker (the
    `nodes.signing_key_id` task), and the key every active node pinned at
    pairing (`Node.pinned_key_id`). A missing key in any of the three is a
    `fail`. Only key ids are reported, never key material."""
    from nodes.keys import SigningKeyUnavailable, signing_keyring

    name = "node_signing_key"
    try:
        web_id = signing_keyring().key_id
    except SigningKeyUnavailable as exc:
        return CheckResult(name, FAIL, _SIGNING_KEY_REASONS.get(exc.reason, exc.reason))
    notes = []
    problems = [
        problem
        for problem in (
            _gateway_key_problem(healthz, web_id, notes),
            _worker_key_problem(web_id, notes),
        )
        if problem
    ]
    # Paired nodes pinned the key handed out at pairing (D4): another active
    # key strands them until they re-pair (rollover = re-pair, Q3).
    stranded = [node for node, _ in active if node.pinned_key_id != web_id]
    if stranded:
        problems.append(
            f"{len(stranded)} active node(s) pinned another key id: re-pair them "
            "(runbook: signing key rollover)"
        )
    detail = f"key id {web_id}"
    if problems:
        return CheckResult(name, FAIL, f"{detail}; " + "; ".join(problems))
    if notes:
        return CheckResult(name, WARN, f"{detail}; " + "; ".join(notes))
    return CheckResult(name, PASS, f"{detail} in web, worker and node-gateway")


def _heartbeat_age(node, now):
    if node.last_heartbeat_at is None:
        return None
    return max(0, int((now - node.last_heartbeat_at).total_seconds()))


def _node_link_result(node, status, now):
    from nodes.selectors import latest_snapshot

    snapshot = latest_snapshot(node)
    entries = list(snapshot.entries or ()) if snapshot is not None else []
    invocable = sum(1 for entry in entries if entry.get("invocable"))
    age = _heartbeat_age(node, now)
    detail = ", ".join(
        [
            status,
            "no heartbeat yet" if age is None else f"heartbeat {age}s ago",
            f"protocol v{node.protocol_version}",
            f"catalogue {snapshot.catalogue_sha256[:12]}" if snapshot else "no catalogue",
            f"{invocable}/{len(entries)} invocable",
        ]
    )
    label = f"node_link:{node.name}"
    if status != "connected" or snapshot is None:
        return CheckResult(label, WARN, detail)
    if age is None or age > NODE_HEARTBEAT_STALE_SECONDS:
        return CheckResult(label, WARN, f"{detail} (stale heartbeat row)")
    return CheckResult(label, PASS, detail)


def _check_node_link(active):
    """Per active node: presence, heartbeat age, protocol, catalogue short
    hash and invocable/advertised counts. Only `connected` with a fresh
    heartbeat row passes; anything else is a `warn` (a sleeping Mac is not a
    broken stack). No paired node is a `warn` too, never a failure: the Mac
    session is deferred."""
    from django.utils import timezone

    if not active:
        return [
            CheckResult("node_link", WARN, "no node paired (Mac session pending owner evaluation)")
        ]
    now = timezone.now()
    return [_node_link_result(node, status, now) for node, status in active]


def _decode_pin(value):
    import base64
    import binascii

    value = value.strip().removeprefix("sha256/")
    if re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return bytes.fromhex(value)
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == 32 else None


def _check_node_tls_pin(name="node_tls_pin"):
    """When `NODE_TLS_PIN_SHA256` is set, the certificate served at
    `PREFLIGHT_NODE_TLS_URL` (the proxy in front of `/ws/node/v1`) has that
    SPKI. A changed certificate key would make every pinned node refuse the
    server. Phase 13's `home_tls_pin` is the same comparison under its own
    row name (D15)."""
    pin = getattr(settings, "NODE_TLS_PIN_SHA256", "")
    if not pin:
        return CheckResult(name, WARN, "NODE_TLS_PIN_SHA256 not set")
    expected = _decode_pin(pin)
    if expected is None:
        return CheckResult(name, FAIL, "NODE_TLS_PIN_SHA256 is not a base64 or hex sha256")
    url = getattr(settings, "PREFLIGHT_NODE_TLS_URL", "")
    if not url:
        return CheckResult(name, WARN, "PREFLIGHT_NODE_TLS_URL not set")
    parsed = urlparse(url)
    try:
        port = parsed.port or 443
    except ValueError:
        port = None
    if parsed.scheme not in ("https", "wss") or not parsed.hostname or port is None:
        return CheckResult(name, FAIL, "PREFLIGHT_NODE_TLS_URL is not an https/wss URL")
    try:
        served = served_spki_sha256(parsed.hostname, port, timeout=_timeout())
    except Exception as exc:
        return CheckResult(name, FAIL, f"TLS handshake failed: {_error_label(exc)}")
    if served != expected:
        return CheckResult(
            name,
            FAIL,
            "the served certificate's SPKI does not match NODE_TLS_PIN_SHA256 "
            "(paired nodes refuse it: restore the key or re-pair)",
        )
    return CheckResult(name, PASS, "served SPKI matches the pin")


def _check_node_audit_chain():
    """`verify_node_audit_chain`: the hash chain is intact."""
    from nodes.audit_chain import verify_chain

    result = verify_chain()
    if not result.ok:
        return CheckResult(
            "node_audit_chain",
            FAIL,
            f"broken at seq {result.broken_seq} ({result.reason}); "
            f"{result.rows} row(s) verified before it",
        )
    return CheckResult("node_audit_chain", PASS, f"{result.rows} row(s) intact")


def _check_node_conformance():
    """`build_node_conformance --check`: the committed case and vector files
    (and the vendored node scrubber) match what the code renders."""
    drift = node_conformance_drift()
    if drift:
        return CheckResult(
            "node_conformance",
            FAIL,
            f"stale: {', '.join(sorted(drift))} (run build_node_conformance)",
        )
    return CheckResult("node_conformance", PASS, "case and vector files current")


def _guarded(name, check):
    """One crashing node check becomes its own `fail` row; the others still
    report."""
    try:
        return check()
    except Exception as exc:
        return CheckResult(name, FAIL, f"check crashed: {_error_label(exc)}")


def check_nodes():
    """Phase 9 configuration and health, read-only (no model call, nothing
    sent to a node): `node_gateway`, `node_signing_key`, `node_link`,
    `node_tls_pin`, `node_audit_chain`, `node_conformance`. Details carry
    key ids, counts, codes and node names only -- never key material, a
    secret, a path or a read output."""
    gateway, healthz = _node_gateway_health()
    results = [gateway]
    try:
        active = _active_nodes()
    except Exception as exc:
        active = None
        results.append(CheckResult("node_link", FAIL, f"check crashed: {_error_label(exc)}"))
    if active is not None:
        results.append(
            _guarded("node_signing_key", lambda: _check_node_signing_key(healthz, active))
        )
        results.extend(_check_node_link(active))
    results.append(_guarded("node_tls_pin", _check_node_tls_pin))
    results.append(_guarded("node_audit_chain", _check_node_audit_chain))
    results.append(_guarded("node_conformance", _check_node_conformance))
    return results


# --- Phase 10: control, with approval (closeout) ----------------------------

# D2/D6: a grant covers delivery plus the node's 60 s confirmation window
# (so at least 70 s) and never exceeds the protocol ceiling (120 s); an
# approved action not dispatched within 60 s is refused, never run later.
NODE_GRANT_TTL_MIN_SECONDS = 70
NODE_GRANT_TTL_MAX_SECONDS = 120
NODE_DISPATCH_DELAY_MAX_SECONDS = 60
# D6: a `node_action` approval lives 10 minutes (owner away = expire).
NODE_ACTION_APPROVAL_TTL_SECONDS = 600
NODE_ACTION_TOOL = "node_action"
# The worker-beat tasks the control path relies on, each at most every
# `max_interval` seconds: the approval expiry sweep (the decision-time check
# is the gate; the sweep is the backstop that expires an unanswered card),
# the node action backstop (stale dispatches, gateways gone) and the daily
# tail retention.
CONTROL_BEAT_TASKS = (
    ("approvals.tasks.sweep_expired_approval_requests", 60),
    ("nodes.tasks.sweep_node_actions_task", 60),
    ("nodes.tasks.clear_node_action_tails_task", 24 * 60 * 60),
)
TRUST_CHECK_PURPOSE = "control.trust_check"
TRUST_CHECK_PROMPT = "friday_control_trust_check"
CONTROL_PENDING_MAC = "pending owner evaluation (Mac session)"


def node_conformance_case_counts():
    """`{protocol version: number of conformance cases}` the suite defines
    (the files `build_node_conformance` writes under `cases/v<N>/`)."""
    from nodes.conformance.cases import all_cases

    return {version: len(cases) for version, cases in all_cases().items()}


def escape_suite_report():
    """`(passed, contained, total)` of `node_escape_suite --executor fake`:
    the D11 cases against the reference generator's policies, the fake
    sandbox model and the runner's caps. Nothing outside a temp dir is
    touched and no `srt` runs."""
    from nodes.conformance.escape.runner import run_suite

    report = run_suite("fake")
    contained = sum(1 for verdict in report.verdicts if verdict.passed)
    return report.passed, contained, len(report.verdicts)


def approve_request_source():
    """The source of `approvals.services.approve_request` (the
    `node_action_actor` source test)."""
    import inspect

    from approvals.services import approve_request

    return inspect.getsource(approve_request)


def _schedule_seconds(schedule):
    if isinstance(schedule, int | float) and not isinstance(schedule, bool):
        return float(schedule)
    run_every = getattr(schedule, "run_every", None)  # celery.schedules.schedule
    if run_every is not None:
        return run_every.total_seconds()
    return None  # a crontab: not an interval this check can bound


def _control_beat_problems():
    schedules, registered = beat_schedules()
    worker = (schedules or {}).get("worker") or {}
    if not worker:
        return ["no worker beat schedule published by the Celery app"]
    problems = []
    for task, max_interval in CONTROL_BEAT_TASKS:
        entries = [entry for entry in worker.values() if entry.get("task") == task]
        if not entries:
            problems.append(f"{task} not on the worker beat")
        else:
            seconds = _schedule_seconds(entries[0].get("schedule"))
            if seconds is None or seconds > max_interval:
                problems.append(f"{task} runs less often than every {max_interval}s")
        if task not in registered:
            problems.append(f"{task} not registered")
    return problems


def _int_setting(name):
    value = getattr(settings, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _check_node_grant_config():
    """D2/D6: `NODE_GRANT_TTL_SECONDS` in 70..120, the dispatch delay at
    most 60 s, `node_action` registered with its 600 s approval TTL (as the
    approvals service computes it, so a lower global cap shows), and the
    control sweeps on the worker beat."""
    from approvals.services import approval_ttl
    from tools.registry import get_tool

    problems = []
    ttl = _int_setting("NODE_GRANT_TTL_SECONDS")
    if ttl is None or not NODE_GRANT_TTL_MIN_SECONDS <= ttl <= NODE_GRANT_TTL_MAX_SECONDS:
        problems.append(
            f"NODE_GRANT_TTL_SECONDS={ttl} is outside "
            f"{NODE_GRANT_TTL_MIN_SECONDS}..{NODE_GRANT_TTL_MAX_SECONDS}"
        )
    delay = _int_setting("NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS")
    if delay is None or not 1 <= delay <= NODE_DISPATCH_DELAY_MAX_SECONDS:
        problems.append(
            f"NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS={delay} is outside "
            f"1..{NODE_DISPATCH_DELAY_MAX_SECONDS}"
        )
    approval_seconds = None
    if get_tool(NODE_ACTION_TOOL) is None:
        problems.append("the node_action tool is not registered")
    else:
        approval_seconds = int(approval_ttl(NODE_ACTION_TOOL).total_seconds())
        if approval_seconds != NODE_ACTION_APPROVAL_TTL_SECONDS:
            problems.append(
                f"a node_action approval lives {approval_seconds}s, not "
                f"{NODE_ACTION_APPROVAL_TTL_SECONDS}s"
            )
    problems.extend(_control_beat_problems())
    if problems:
        return CheckResult("node_grant_config", FAIL, "; ".join(problems))
    return CheckResult(
        "node_grant_config",
        PASS,
        f"grant TTL {ttl}s, dispatch delay {delay}s, node_action approval "
        f"{approval_seconds}s, {len(CONTROL_BEAT_TASKS)} control sweeps on the worker beat",
    )


def _protocol_v2_row(node, status):
    """`(row, speaks_v2)` for one active node."""
    from nodes.selectors import latest_snapshot
    from nodes.services import snapshot_protocol_version

    snapshot = latest_snapshot(node)
    label = f"node_protocol_v2:{node.name}"
    if snapshot is None:
        return CheckResult(label, WARN, f"{status}, no catalogue yet"), False
    version = snapshot_protocol_version(snapshot)
    counts = Counter(
        entry.get("class") for entry in (snapshot.entries or ()) if entry.get("invocable")
    )
    detail = (
        f"{status}, protocol v{version or '?'}, {counts['gate']} gate / "
        f"{counts['confirm']} confirm invocable"
    )
    if version != 2:
        return CheckResult(label, WARN, f"{detail} (actions need a v2 session)"), False
    if not counts["gate"] + counts["confirm"]:
        return CheckResult(label, WARN, f"{detail} (no action invocable)"), True
    return CheckResult(label, PASS, detail), True


def _check_node_protocol_v2():
    """Per active node: the session version its latest catalogue was
    classified for and how many gate- and confirm-class capabilities are
    invocable. A `warn`, never a failure, when no node speaks v2: the Swift
    app is the deferred Mac session."""
    active = _active_nodes()
    if not active:
        return [CheckResult("node_protocol_v2", WARN, f"no node paired ({CONTROL_PENDING_MAC})")]
    rows, speaks_v2 = [], False
    for node, status in active:
        row, is_v2 = _protocol_v2_row(node, status)
        rows.append(row)
        speaks_v2 = speaks_v2 or is_v2
    if not speaks_v2:
        rows.append(
            CheckResult(
                "node_protocol_v2", WARN, f"no node speaks protocol v2 ({CONTROL_PENDING_MAC})"
            )
        )
    return rows


def _check_node_conformance_v2():
    """`build_node_conformance --check` for the v1 and v2 cases and
    `vectors/sandbox.json` (what the Swift test target loads, M1/M2)."""
    counts = node_conformance_case_counts()
    missing = [f"v{version}" for version in (1, 2) if not counts.get(version)]
    if missing:
        return CheckResult(
            "node_conformance_v2", FAIL, f"no {' or '.join(missing)} conformance cases defined"
        )
    drift = sorted(
        path
        for path in node_conformance_drift()
        if path.startswith(("cases/v1/", "cases/v2/")) or path == "vectors/sandbox.json"
    )
    if drift:
        shown = ", ".join(drift[:5]) + (f" (+{len(drift) - 5} more)" if len(drift) > 5 else "")
        return CheckResult(
            "node_conformance_v2", FAIL, f"stale: {shown} (run build_node_conformance)"
        )
    return CheckResult(
        "node_conformance_v2",
        PASS,
        f"{counts[1]} v1 + {counts[2]} v2 cases and vectors/sandbox.json current",
    )


def _escape_evidence():
    """`(status, note)` of the recorded ADR-014 Mac evidence date."""
    import datetime

    raw = str(getattr(settings, "NODE_ESCAPE_EVIDENCE_RECORDED_ON", "") or "").strip()
    if not raw:
        return WARN, "Mac evidence (ADR-014, M3) pending owner evaluation"
    try:
        recorded = datetime.date.fromisoformat(raw)
    except ValueError:
        return FAIL, "NODE_ESCAPE_EVIDENCE_RECORDED_ON is not an ISO date"
    return PASS, f"Mac evidence (ADR-014, M3) recorded {recorded.isoformat()}"


def _check_node_escape_suite():
    """`node_escape_suite --executor fake` contains every case; the ADR-014
    evidence date (the macOS `srt` run, M3) is shown, a `warn` while it is
    pending owner evaluation."""
    passed, contained, total = escape_suite_report()
    if not passed:
        return CheckResult(
            "node_escape_suite",
            FAIL,
            f"{contained}/{total} contained with the fake executor (run node_escape_suite)",
        )
    status, note = _escape_evidence()
    return CheckResult(
        "node_escape_suite",
        status,
        f"{contained}/{total} contained with the fake executor; {note}",
    )


def _check_node_output_scrubber():
    """The node-gateway reports a registered output scrubber in /healthz
    (without one every output chunk and tail is the fail-closed marker)."""
    name = "node_output_scrubber"
    gateway, healthz = _node_gateway_health()
    if healthz is None:
        return CheckResult(name, gateway.status, f"not checked: {gateway.detail}")
    registered = healthz.get("output_scrubber")
    if registered is True:
        return CheckResult(name, PASS, "the node-gateway has its output scrubber registered")
    if registered is False:
        return CheckResult(
            name,
            FAIL,
            "the node-gateway has no output scrubber (every output is the fail-closed marker)",
        )
    return CheckResult(name, FAIL, "the node-gateway's /healthz does not report its scrubber")


def _callable_name(fn):
    qualname = getattr(fn, "__qualname__", type(fn).__name__)
    return f"{getattr(fn, '__module__', '?')}.{qualname}"


def _check_trust_check():
    """D9: the trust check and its evidence source are registered, and the
    registered check is not the branch-3 stub (`is_stub`); the model step's
    purpose has a tier, an active budget and a seeded prompt."""
    from ai_core.budgets import budgets_for
    from ai_core.models import PromptTemplate
    from tools import node_action_ports

    name = "trust_check"
    check = node_action_ports.registered_trust_check()
    if check is None:
        return CheckResult(name, FAIL, "no trust check registered (every node_action is blocked)")
    if getattr(check, "is_stub", False):
        return CheckResult(
            name,
            FAIL,
            "the branch-3 stub is the registered trust check (warn:trust_check_pending)",
        )
    problems = []
    if node_action_ports.evidence_source() is None:
        problems.append("no evidence source registered")
    tier = (getattr(settings, "AI_PURPOSE_TIERS", {}) or {}).get(TRUST_CHECK_PURPOSE)
    if not tier:
        problems.append(f"{TRUST_CHECK_PURPOSE} has no tier in AI_PURPOSE_TIERS")
    budgets = budgets_for(TRUST_CHECK_PURPOSE)
    if not budgets:
        problems.append(f"no active budget covers {TRUST_CHECK_PURPOSE} (run migrate)")
    template = (
        PromptTemplate.objects.filter(name=TRUST_CHECK_PROMPT)
        .select_related("active_version")
        .first()
    )
    if template is None or template.active_version is None:
        problems.append(f"{TRUST_CHECK_PROMPT} is not seeded (run seed_ai_defaults)")
    if problems:
        return CheckResult(name, FAIL, "; ".join(problems))
    described = "; ".join(_describe_budget(b) for b in sorted(budgets, key=lambda b: b.name))
    detail = f"{_callable_name(check)}, tier {tier}, {described}"
    if all(b.monthly_limit_usd is None for b in budgets):
        return CheckResult(name, WARN, f"{detail} (no monthly cap)")
    return CheckResult(name, PASS, detail)


def _approve_calls_hook_before_status_change(source):
    """True when `authorize_decision(...)` is called in `source` before the
    first assignment of a `.status` attribute (the decision)."""
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    hook_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "authorize_decision"
    ]
    status_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "status" for t in node.targets)
    ]
    if not hook_lines:
        return False
    return not status_lines or min(hook_lines) < min(status_lines)


def _check_node_action_actor():
    """D6: `node_action` declares the owner-only hooks (`precheck_request`
    before any row or model call, `authorize_decision` on approval), and
    `approve_request` calls the approver hook before the status changes
    (a source test, as the brief asks)."""
    from tools.registry import get_tool

    name = "node_action_actor"
    tool = get_tool(NODE_ACTION_TOOL)
    if tool is None:
        return CheckResult(name, FAIL, "the node_action tool is not registered")
    missing = [
        hook
        for hook in ("precheck_request", "authorize_decision")
        if not callable(getattr(tool, hook, None))
    ]
    if missing:
        return CheckResult(name, FAIL, f"node_action declares no {', '.join(missing)}")
    if not _approve_calls_hook_before_status_change(approve_request_source()):
        return CheckResult(
            name, FAIL, "approve_request does not call authorize_decision before deciding"
        )
    return CheckResult(
        name,
        PASS,
        "precheck_request and authorize_decision declared; approve_request calls the hook",
    )


def _harness_driver_row(row):
    label = f"harness_drivers:{row['agent']}"
    driver = row["driver"]
    if not row["known"]:
        return CheckResult(label, FAIL, f"unknown driver {driver!r}")
    if driver == "cli":
        return CheckResult(label, PASS, "disabled (CLI path)")
    if not row["enabled"]:
        return CheckResult(label, FAIL, f"{driver} selected while HARNESS_DRIVERS_ENABLED is off")
    problems = []
    if row["terms_checked_on"] is None:
        problems.append("d4 not recorded (HARNESS_DRIVER_TERMS_CHECKED_ON)")
    if row["provisional"]:
        problems.append(
            f"the snapshot for pin {row['pin']} is provisional (run generate_driver_schemas.sh)"
        )
    if row["pin_errors"]:
        problems.append(f"{len(row['pin_errors'])} pin problem(s): pin, ARG and snapshot differ")
    if problems:
        return CheckResult(label, FAIL, f"{driver} enabled; " + "; ".join(problems))
    return CheckResult(
        label,
        PASS,
        f"{driver} enabled, d4 {row['terms_checked_on'].isoformat()}, pin {row['pin']}",
    )


def _check_harness_drivers():
    """D10: per driver, disabled (the CLI path) or enabled with d4
    recorded, a non-provisional snapshot and pins that agree. The
    `harness.W002` system check only warns on a provisional snapshot; this
    check fails on it."""
    from harness.drivers import driver_status

    return [_harness_driver_row(row) for row in driver_status()]


def _guarded_rows(name, check):
    """`_guarded` for a check that returns a list of rows."""
    try:
        return list(check())
    except Exception as exc:
        return [CheckResult(name, FAIL, f"check crashed: {_error_label(exc)}")]


def check_control():
    """Phase 10 configuration and health, read-only (no model call, nothing
    sent to a node, nothing executed outside a temp dir): `node_grant_config`,
    `node_protocol_v2`, `node_conformance_v2`, `node_escape_suite`,
    `node_output_scrubber`, `trust_check`, `node_action_actor` and
    `harness_drivers`. Details carry settings names, counts, codes, node
    names and dates -- never args, output, a path or a secret."""
    return [
        _guarded("node_grant_config", _check_node_grant_config),
        *_guarded_rows("node_protocol_v2", _check_node_protocol_v2),
        _guarded("node_conformance_v2", _check_node_conformance_v2),
        _guarded("node_escape_suite", _check_node_escape_suite),
        _guarded("node_output_scrubber", _check_node_output_scrubber),
        _guarded("trust_check", _check_trust_check),
        _guarded("node_action_actor", _check_node_action_actor),
        *_guarded_rows("harness_drivers", _check_harness_drivers),
    ]


# --- Phase 11: local live voice (closeout) ----------------------------------

# D4: the only DRF views a `node_voice` token may reach -- the chat turn,
# the three voice endpoints, live sessions and the policy read. Named, not
# imported: `voice` is a leaf whose contract lists `ops` (ADR-002), so this
# module compares the routed view classes by their dotted names.
NODE_VOICE_SURFACES = frozenset(
    {
        "assistant.views.ConversationMessageListView",
        "voice.views.TranscriptionCreateView",
        "voice.views.SpeechCreateView",
        "voice.views.UsageReceiptView",
        "voice.views.LiveSessionCreateView",
        "accounts.views.NodeVoicePolicyView",
    }
)
VOICE_LIVE_TOKEN_TTL_MAX_SECONDS = 3600
# D13/M3: a green nightly self-trigger result older than this is stale.
VOICE_LIVE_SELFTEST_MAX_AGE_HOURS = 48
VOICE_LIVE_SELFTEST_SCHEMA = "friday-voice/selftest/v1"
VOICE_LIVE_PENDING_MAC = "Mac session pending owner evaluation"
FRIDAY_VOICE_LOCK = "conformance.lock"
# What `friday-voice conformance --build` writes and the lock must cover:
# the case files of both families, the frame vectors and the v1 schemas.
FRIDAY_VOICE_LOCKED_GLOBS = (
    "friday_voice/conformance/cases/*/*.json",
    "friday_voice/protocol/vectors/frames.json",
    "friday_voice/protocol/schemas/v1/*/*.schema.json",
)
_LOCK_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_./-]+)$")


def drf_routes():
    """Every routed DRF view of the running URL table, as
    `(route, view_class, authentication classes)` -- the same walk the
    route-walk tests make (`config/tests/test_node_voice_surfaces.py`),
    at runtime, so a view added after the tests ran still shows."""
    from django.urls import URLPattern, URLResolver, get_resolver
    from rest_framework.views import APIView

    def walk(resolver, prefix):
        for pattern in resolver.url_patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                yield from walk(pattern, route)
            elif isinstance(pattern, URLPattern):
                view_class = getattr(pattern.callback, "view_class", None)
                if view_class is None or not issubclass(view_class, APIView):
                    continue
                initkwargs = getattr(pattern.callback, "view_initkwargs", {}) or {}
                auth = initkwargs.get("authentication_classes", view_class.authentication_classes)
                yield route, view_class, list(auth)

    return list(walk(get_resolver(), ""))


def friday_voice_dir():
    """The `node/friday-voice` checkout preflight hashes, or None when this
    host has none (the backend image does not ship `node/`)."""
    configured = getattr(settings, "PREFLIGHT_FRIDAY_VOICE_DIR", "") or ""
    path = (
        Path(configured)
        if configured
        else Path(settings.BASE_DIR).resolve().parent / "node" / "friday-voice"
    )
    return path if path.is_dir() else None


def _view_name(view_class):
    return f"{view_class.__module__}.{view_class.__qualname__}"


def _accepts_kind(auth_classes, kind):
    from accounts.authentication import PersonalAccessTokenAuthentication

    return any(
        isinstance(cls, type)
        and issubclass(cls, PersonalAccessTokenAuthentication)
        and kind in cls().get_accepted_kinds()
        for cls in auth_classes
    )


def _check_voice_live_channel():
    """D3/D4: the `node_voice` kind exists, maps to the node voice channel
    and is not mintable through `/auth/tokens`; the routed views whose
    authenticators accept it are exactly the D4 list (a walk of the live
    URL table); the token lifetime is at most one hour."""
    from accounts.authentication import KIND_CHANNELS, NodeVoiceTokenAuthentication
    from accounts.models import MINTABLE_PAT_KINDS, PersonalAccessToken
    from core.channels import CHANNEL_NODE_VOICE

    name = "voice_live_channel"
    kind = getattr(PersonalAccessToken.Kind, "NODE_VOICE", None)
    if kind is None:
        return CheckResult(name, FAIL, "the node_voice token kind is not registered")
    problems = []
    if KIND_CHANNELS.get(kind) != CHANNEL_NODE_VOICE:
        problems.append("node_voice does not authenticate as the node_voice channel")
    if kind in MINTABLE_PAT_KINDS:
        problems.append("node_voice is mintable through /auth/tokens")
    if kind not in NodeVoiceTokenAuthentication().get_accepted_kinds():
        problems.append("NodeVoiceTokenAuthentication does not accept node_voice")
    accepting = {
        _view_name(view) for _route, view, auth in drf_routes() if _accepts_kind(auth, kind)
    }
    extra = sorted(accepting - NODE_VOICE_SURFACES)
    missing = sorted(NODE_VOICE_SURFACES - accepting)
    if extra:
        problems.append(f"node_voice reaches {len(extra)} view(s) outside D4: {', '.join(extra)}")
    if missing:
        problems.append(f"{len(missing)} D4 view(s) not routed with it: {', '.join(missing)}")
    ttl = _int_setting("VOICE_LIVE_TOKEN_TTL_SECONDS")
    if ttl is None or not 0 < ttl <= VOICE_LIVE_TOKEN_TTL_MAX_SECONDS:
        problems.append(
            f"VOICE_LIVE_TOKEN_TTL_SECONDS={ttl} is outside 1..{VOICE_LIVE_TOKEN_TTL_MAX_SECONDS}"
        )
    if problems:
        return CheckResult(name, FAIL, "; ".join(problems))
    return CheckResult(
        name,
        PASS,
        f"node_voice reaches exactly the {len(NODE_VOICE_SURFACES)} D4 views; token TTL {ttl}s",
    )


def _verdict_evidence_reasons(entry):
    """A model's D11 reasons apart from `disabled` (the setting is judged
    separately here): empty when its latest household + positives pair
    passes at the threshold it was measured at."""
    return [reason for reason in entry["verdict"].reasons if reason != "disabled"]


def _check_voice_live_policy():
    """D11 judged exactly as the policy endpoint judges it: the same
    `accounts.voice_live.model_verdicts` (the LATEST row per model and kind,
    the pair at the same threshold), never a copy. `warn` while no model's
    latest pair passes (pending owner evaluation), `pass` when one does,
    `fail` when VOICE_LIVE_ALWAYS_ON_ENABLED is on and none does."""
    from accounts import voice_live

    name = "voice_live_policy"
    enabled = voice_live.always_on_enabled()
    workspace = _installation_owner_workspace()
    verdicts = (
        voice_live.model_verdicts(workspace.owner, workspace.pk) if workspace is not None else []
    )
    passing = [entry for entry in verdicts if not _verdict_evidence_reasons(entry)]
    setting = f"VOICE_LIVE_ALWAYS_ON_ENABLED={'on' if enabled else 'off'}"
    if passing:
        best = passing[0]
        detail = (
            f"{setting}; model {best['model_sha256'][:12]} passes at threshold "
            f"{best['threshold']} / {best['refractory_ms']} ms"
        )
        if not enabled:
            detail += " (always-on stays off until the setting is enabled)"
        return CheckResult(name, PASS, detail)
    reasons = sorted(
        {reason for entry in verdicts for reason in _verdict_evidence_reasons(entry)}
    ) or [voice_live.REASON_NO_HOUSEHOLD, voice_live.REASON_NO_POSITIVES]
    measured = f"{len(verdicts)} wake model(s) measured"
    if enabled:
        return CheckResult(
            name,
            FAIL,
            f"{setting} but no model's latest evaluation pair passes: "
            f"{', '.join(reasons)} ({measured})",
        )
    return CheckResult(
        name,
        WARN,
        f"always-on not permitted: {', '.join(reasons)} ({measured}; {VOICE_LIVE_PENDING_MAC})",
    )


def _age_label(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 2 * 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def _check_voice_live_storm():
    """D12: an unreleased `accounts.VoiceLiveMute` is a `warn` with its age
    (the owner releases it on screen, /voice). Whole installation: `ops` is
    operations tooling, nothing here reaches a view or an agent tool."""
    from django.utils import timezone

    from accounts.models import VoiceLiveMute

    engaged = list(
        VoiceLiveMute.objects.filter(released_at__isnull=True)
        .select_related("node_device")
        .order_by("engaged_at")
    )
    if not engaged:
        return [CheckResult("voice_live_storm", PASS, "no storm mute engaged")]
    now = timezone.now()
    return [
        CheckResult(
            f"voice_live_storm:{mute.node_device.name}",
            WARN,
            f"storm mute ({mute.reason}) engaged "
            f"{_age_label((now - mute.engaged_at).total_seconds())} ago: "
            "release it on screen (/voice)",
        )
        for mute in engaged
    ]


def _parse_selftest_line(text):
    """The last non-empty line of the result file as the `friday-voice/
    selftest/v1` document, or None when it is not one."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        document = json.loads(lines[-1])
    except ValueError:
        return None
    if not isinstance(document, dict) or document.get("schema") != VOICE_LIVE_SELFTEST_SCHEMA:
        return None
    if document.get("result") not in ("pass", "fail", "error"):
        return None
    return document


def _check_voice_live_selftest():
    """D13/M3: the nightly self-trigger's one-line result
    (`VOICE_LIVE_SELFTEST_RESULT_PATH`, written by `friday-voice selftest
    --nightly --result-file`): `pass` when green and younger than 48 h,
    `warn` when not configured, missing or stale (the Mac is pending or
    asleep), `fail` when the last run failed or could not judge."""
    import datetime

    from django.utils import timezone

    name = "voice_live_selftest"
    configured = getattr(settings, "VOICE_LIVE_SELFTEST_RESULT_PATH", "") or ""
    if not configured:
        return CheckResult(
            name, WARN, f"VOICE_LIVE_SELFTEST_RESULT_PATH not set ({VOICE_LIVE_PENDING_MAC})"
        )
    path = Path(configured).expanduser()
    if not path.exists():
        return CheckResult(name, WARN, f"no nightly result yet ({VOICE_LIVE_PENDING_MAC})")
    try:
        document = _parse_selftest_line(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return CheckResult(name, FAIL, f"result file unreadable: {_error_label(exc)}")
    if document is None:
        return CheckResult(
            name, FAIL, f"the result file is not a {VOICE_LIVE_SELFTEST_SCHEMA} line"
        )
    try:
        at = datetime.datetime.fromisoformat(str(document.get("at", "")).replace("Z", "+00:00"))
    except ValueError:
        return CheckResult(name, FAIL, "the result line has no valid `at` timestamp")
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.UTC)
    age = (timezone.now() - at).total_seconds()
    mode = document.get("mode", "?")
    wakes = int(document.get("wakes") or 0)
    posted = int(document.get("posted_turns") or 0)
    counts = f"wakes {wakes}, posted turns {posted}"
    detail = f"{mode} run {_age_label(age)} ago: {document['result']} ({counts})"
    if document["result"] != "pass":
        reason = str(document.get("reason") or "")[:60]
        return CheckResult(name, FAIL, detail + (f", reason {reason}" if reason else ""))
    if age < -300:
        return CheckResult(name, FAIL, "the result line is dated in the future")
    if mode != "nightly":
        return CheckResult(name, WARN, f"{detail} (not a nightly run; {VOICE_LIVE_PENDING_MAC})")
    if age > VOICE_LIVE_SELFTEST_MAX_AGE_HOURS * 3600:
        return CheckResult(
            name, WARN, f"{detail} (stale: older than {VOICE_LIVE_SELFTEST_MAX_AGE_HOURS} h)"
        )
    return CheckResult(name, PASS, detail)


def _read_lock(path):
    """`{relative path: sha256}` of a conformance.lock, or None when a line
    is malformed."""
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = _LOCK_LINE.match(line)
        if match is None or ".." in match.group(2).split("/"):
            return None
        entries[match.group(2)] = match.group(1)
    return entries


def _sha256_file(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_friday_voice_conformance():
    """The `friday-voice/v1` case, vector and schema files in the checkout
    match `node/friday-voice/conformance.lock` (written by `friday-voice
    conformance --build`, checked by `--check` in CI). Hashes only: the
    backend imports nothing from the child. Where the checkout is absent
    (the backend image ships no `node/`), a `warn`, never a failure."""
    name = "friday_voice_conformance"
    root = friday_voice_dir()
    if root is None:
        return CheckResult(name, WARN, "conformance not checkable here (no node/friday-voice)")
    lock = root / FRIDAY_VOICE_LOCK
    if not lock.is_file():
        return CheckResult(
            name, FAIL, f"{FRIDAY_VOICE_LOCK} missing (run friday-voice conformance --build)"
        )
    entries = _read_lock(lock)
    if not entries:
        return CheckResult(name, FAIL, f"{FRIDAY_VOICE_LOCK} is malformed or empty")
    on_disk = {
        path.relative_to(root).as_posix()
        for pattern in FRIDAY_VOICE_LOCKED_GLOBS
        for path in root.glob(pattern)
    }
    drift = sorted(
        rel
        for rel, digest in entries.items()
        if not (root / rel).is_file() or _sha256_file(root / rel) != digest
    )
    drift += sorted(on_disk - set(entries))
    if drift:
        shown = ", ".join(drift[:5]) + (f" (+{len(drift) - 5} more)" if len(drift) > 5 else "")
        return CheckResult(
            name,
            FAIL,
            f"drift against {FRIDAY_VOICE_LOCK}: {shown} (run friday-voice conformance --check)",
        )
    families = Counter(
        rel.split("/")[3] for rel in entries if rel.startswith("friday_voice/conformance/cases/")
    )
    return CheckResult(
        name,
        PASS,
        f"{len(entries)} files match {FRIDAY_VOICE_LOCK} ({families['host']} host + "
        f"{families['child']} child cases, vectors, schemas)",
    )


def _voice_state_row(node, status, now):
    """`(row, reported)` for one active node's `voice.state`."""
    from nodes.selectors import voice_summary

    label = f"node_voice_state:{node.name}"
    if node.voice_state_at is None:
        return (
            CheckResult(
                label,
                WARN,
                f"{status}, protocol v{node.protocol_version}, no voice.state reported yet",
            ),
            False,
        )
    summary = voice_summary(node, status=status, online=status == "connected", now=now)
    age = _age_label((now - node.voice_state_at).total_seconds())
    detail = (
        f"{status}, state {summary['state']} (reported {node.voice_state} {age} ago), "
        f"mode {node.voice_mode or '-'}, wake accepts {node.voice_wake_accepts_today}, "
        f"discards {node.voice_wake_discards_today}, storm mutes {node.voice_storm_mutes_today}"
    )
    return CheckResult(label, PASS, detail), True


def _check_node_voice_state():
    """D15: per active node, whether it reports `voice.state` (protocol v2)
    and what the HUD shows from it. A `warn`, never a failure, when no node
    does: the Swift voice host is the deferred Mac session (M1)."""
    from django.utils import timezone

    active = _active_nodes()
    if not active:
        return [CheckResult("node_voice_state", WARN, f"no node paired ({VOICE_LIVE_PENDING_MAC})")]
    now = timezone.now()
    rows, reported = [], False
    for node, status in active:
        row, has_state = _voice_state_row(node, status, now)
        rows.append(row)
        reported = reported or has_state
    if not reported:
        rows.append(
            CheckResult(
                "node_voice_state", WARN, f"no node reports voice.state ({VOICE_LIVE_PENDING_MAC})"
            )
        )
    return rows


def check_live_voice():
    """Phase 11 configuration and health, read-only (no model call, nothing
    sent to a node, no `voice` import): `voice_live_channel`,
    `voice_live_policy`, `voice_live_storm`, `voice_live_selftest`,
    `friday_voice_conformance` and `node_voice_state`. Details carry setting
    names, codes, counts, short hashes, ages and node names -- never a
    token, a transcript, a path or audio."""
    return [
        _guarded("voice_live_channel", _check_voice_live_channel),
        _guarded("voice_live_policy", _check_voice_live_policy),
        *_guarded_rows("voice_live_storm", _check_voice_live_storm),
        _guarded("voice_live_selftest", _check_voice_live_selftest),
        _guarded("friday_voice_conformance", _check_friday_voice_conformance),
        *_guarded_rows("node_voice_state", _check_node_voice_state),
    ]


# --- Phase 12: reach and proactivity (closeout) ---------------------------

# D3: the one runner of owner schedules and the notice prune, both on the
# worker beat (config/celery.py); the heartbeat at least once a minute.
PROACTIVE_BEAT_TASKS = (
    ("proactive.tasks.heartbeat", 60),
    ("proactive.tasks.prune", 24 * 60 * 60),
)
# D16/D17: the weekly curator and the nightly playbook distiller (crontabs).
SKILLS_BEAT_TASKS = (
    ("skills.tasks.curate", None),
    ("skills.tasks.distill_playbooks", None),
)
PROACTIVE_DIGEST_PURPOSE = "proactive.digest"
PROACTIVE_DIGEST_PROMPT = "friday_proactive_digest"
# D15-D18: each skills purpose and its seeded prompt (ai_core 0010/0012/0013).
SKILLS_PURPOSE_PROMPTS = (
    ("skills.match", "friday_skills_match"),
    ("skills.curate", "friday_skills_curate"),
    ("skills.playbook_distill", "friday_skills_playbook_distill"),
)
# An enabled schedule whose `next_run_at` is further in the past than this
# was not claimed by the heartbeat (which runs every 60 s): the beat or the
# worker is down.
PROACTIVE_STALE_SECONDS = 10 * 60
# A run still `running` after this long lost its worker.
PROACTIVE_RUN_STUCK_SECONDS = 15 * 60
PROACTIVE_OWNER_STEP = "owner step: enable morning_digest on /today"
N8N_CONNECTOR = "n8n"
N8N_OWNER_STEP = "not configured (Phase 12 owner step 3)"


def n8n_relay_allowed_tools_setting():
    """The relay ceiling the deployment hands the `n8n-relay` sidecar
    (`N8N_RELAY_ALLOWED_TOOLS`; compose passes the same variable, with the
    same default, to web and worker -- names only), as a set of server
    tool names."""
    value = getattr(settings, "N8N_RELAY_ALLOWED_TOOLS", "") or ""
    return {name.strip() for name in str(value).split(",") if name.strip()}


def _worker_beat_problems(tasks):
    """`[problem, ...]` for `(task, max_interval_seconds | None)` pairs on
    the worker beat (None: any schedule, e.g. a crontab)."""
    schedules, registered = beat_schedules()
    worker = (schedules or {}).get("worker") or {}
    if not worker:
        return ["no worker beat schedule published by the Celery app"]
    problems = []
    for task, max_interval in tasks:
        entries = [entry for entry in worker.values() if entry.get("task") == task]
        if not entries:
            problems.append(f"{task} not on the worker beat")
        elif max_interval is not None:
            seconds = _schedule_seconds(entries[0].get("schedule"))
            if seconds is None or seconds > max_interval:
                problems.append(f"{task} runs less often than every {max_interval}s")
        if task not in registered:
            problems.append(f"{task} not registered")
    return problems


def _purpose_seeding(purpose, prompt_name):
    """`(problems, budgets)` for one gateway purpose: a tier in
    `AI_PURPOSE_TIERS`, an active budget, a seeded prompt with an active
    version."""
    from ai_core.budgets import budgets_for
    from ai_core.models import PromptTemplate

    problems = []
    tier = (getattr(settings, "AI_PURPOSE_TIERS", {}) or {}).get(purpose)
    if not tier:
        problems.append(f"{purpose} has no tier in AI_PURPOSE_TIERS")
    budgets = budgets_for(purpose)
    if not budgets:
        problems.append(f"no active budget covers {purpose} (run migrate)")
    template = (
        PromptTemplate.objects.filter(name=prompt_name).select_related("active_version").first()
    )
    if template is None or template.active_version is None:
        problems.append(f"{prompt_name} is not seeded (run seed_ai_defaults)")
    return problems, budgets


def _seeded_row(name, pairs):
    """One row over `(purpose, prompt)` pairs: `fail` on a missing tier,
    budget or prompt, `warn` when a purpose has no monthly cap."""
    problems, budgets, uncapped = [], {}, []
    for purpose, prompt_name in pairs:
        found, covering = _purpose_seeding(purpose, prompt_name)
        problems.extend(found)
        budgets.update({b.pk: b for b in covering})
        if covering and all(b.monthly_limit_usd is None for b in covering):
            uncapped.append(purpose)
    if problems:
        return CheckResult(name, FAIL, "; ".join(problems))
    described = "; ".join(
        _describe_budget(b) for b in sorted(budgets.values(), key=lambda b: b.name)
    )
    tiers = getattr(settings, "AI_PURPOSE_TIERS", {}) or {}
    detail = ", ".join(f"{p} ({tiers[p]})" for p, _ in pairs) + f"; {described}"
    if uncapped:
        return CheckResult(name, WARN, f"no monthly cap on {', '.join(uncapped)}: {detail}")
    return CheckResult(name, PASS, detail)


def _check_proactive_beat():
    problems = _worker_beat_problems(PROACTIVE_BEAT_TASKS)
    if problems:
        return CheckResult("proactive_beat", FAIL, "; ".join(problems))
    return CheckResult(
        "proactive_beat", PASS, "heartbeat every <= 60s and the notice prune on the worker beat"
    )


def _enabled_schedules():
    from proactive.models import ProactiveSchedule

    return list(
        ProactiveSchedule.objects.filter(enabled=True)
        .select_related("workspace")
        .order_by("workspace__created_at", "kind")
    )


def _schedule_label(schedule):
    return f"{schedule.kind}@{schedule.workspace.type}"


def _check_proactive_schedules(enabled, now):
    """D3: every enabled schedule has a `next_run_at` the heartbeat will
    claim; one past `PROACTIVE_STALE_SECONDS` means the beat is not running."""
    from datetime import timedelta

    from proactive.models import ProactivePause

    if not enabled:
        return CheckResult(
            "proactive_schedules", WARN, f"no schedule enabled ({PROACTIVE_OWNER_STEP})"
        )
    stale_before = now - timedelta(seconds=PROACTIVE_STALE_SECONDS)
    incomplete = [_schedule_label(s) for s in enabled if s.next_run_at is None]
    stale = [_schedule_label(s) for s in enabled if s.next_run_at and s.next_run_at < stale_before]
    paused = set(
        ProactivePause.objects.filter(
            workspace_id__in={s.workspace_id for s in enabled}
        ).values_list("workspace_id", flat=True)
    )
    detail = f"{len(enabled)} enabled: " + ", ".join(_schedule_label(s) for s in enabled)
    if paused:
        detail += f" ({len(paused)} workspace(s) paused)"
    problems = []
    if incomplete:
        problems.append(f"no next run (incomplete): {', '.join(incomplete)}")
    if stale:
        problems.append(
            f"next run more than {PROACTIVE_STALE_SECONDS // 60} min overdue "
            f"(is the heartbeat running?): {', '.join(stale)}"
        )
    if problems:
        return CheckResult("proactive_schedules", FAIL, f"{detail}; " + "; ".join(problems))
    return CheckResult("proactive_schedules", PASS, detail)


def _latest_runs(enabled):
    from proactive.models import ProactiveRun

    latest = {}
    for schedule in enabled:
        run = ProactiveRun.objects.filter(schedule=schedule).order_by("-started_at").first()
        if run is not None:
            latest[schedule.pk] = run
    return latest


def _check_proactive_last_run(enabled, latest, now):
    """The last run of each enabled schedule is not `failed` (`fail`); a
    run `running` past `PROACTIVE_RUN_STUCK_SECONDS` lost its worker and a
    schedule with no run yet are a `warn`."""
    from datetime import timedelta

    from proactive.models import ProactiveRun

    if not enabled:
        return CheckResult("proactive_last_run", WARN, "no schedule enabled")
    outcome = ProactiveRun.Outcome
    stuck_before = now - timedelta(seconds=PROACTIVE_RUN_STUCK_SECONDS)
    failed, stuck, never, done = [], [], [], []
    for schedule in enabled:
        label = _schedule_label(schedule)
        run = latest.get(schedule.pk)
        if run is None:
            never.append(label)
        elif run.outcome == outcome.FAILED:
            failed.append(label)
        elif run.outcome == outcome.RUNNING and run.started_at < stuck_before:
            stuck.append(label)
        else:
            done.append(f"{label} {run.outcome}")
    if failed:
        return CheckResult("proactive_last_run", FAIL, f"last run failed: {', '.join(failed)}")
    notes = []
    if stuck:
        notes.append(
            f"running for more than {PROACTIVE_RUN_STUCK_SECONDS // 60} min: {', '.join(stuck)}"
        )
    if never:
        notes.append(f"no run yet: {', '.join(never)}")
    if notes:
        return CheckResult("proactive_last_run", WARN, "; ".join(notes))
    return CheckResult("proactive_last_run", PASS, ", ".join(done))


def _check_proactive_digest():
    return _seeded_row("proactive_digest", ((PROACTIVE_DIGEST_PURPOSE, PROACTIVE_DIGEST_PROMPT),))


def _check_proactive_probes(latest):
    """The registered probes (boot-validated), and no probe the last run of
    an enabled schedule reported slow or failed (`warn`: the run degraded
    gracefully, the probe needs attention; a denial is the guard working)."""
    from core import probes

    names = [spec.name for spec in probes.list_probes()]
    if not names:
        return CheckResult("proactive_probes", FAIL, "no probe registered")
    slow = sorted({name for run in latest.values() for name in (run.slow_probes or [])})
    failing = sorted(
        {
            f"{name}={code}"
            for run in latest.values()
            for name, code in (run.probe_failures or {}).items()
            if code != "denied"
        }
    )
    detail = f"{len(names)} probes registered"
    notes = []
    if slow:
        notes.append(f"slow on the last run: {', '.join(slow)}")
    if failing:
        notes.append(f"failed on the last run: {', '.join(failing)}")
    if notes:
        return CheckResult("proactive_probes", WARN, f"{detail}; " + "; ".join(notes))
    return CheckResult("proactive_probes", PASS, f"{detail}, none slow on the last runs")


def check_proactive():
    """Phase 12 D1-D6 configuration and health, read-only (no model call,
    no run started, nothing written): `proactive_beat`, `proactive_schedules`,
    `proactive_last_run`, `proactive_digest` and `proactive_probes`. Details
    carry schedule kinds, workspace types, probe names, closed codes and
    counts -- never a title, a label, digest text or a workspace name."""
    from django.utils import timezone

    now = timezone.now()
    try:
        enabled = _enabled_schedules()
        latest = _latest_runs(enabled)
    except Exception as exc:
        crashed = f"check crashed: {_error_label(exc)}"
        return [
            _guarded("proactive_beat", _check_proactive_beat),
            CheckResult("proactive_schedules", FAIL, crashed),
            CheckResult("proactive_last_run", FAIL, crashed),
            _guarded("proactive_digest", _check_proactive_digest),
            CheckResult("proactive_probes", FAIL, crashed),
        ]
    return [
        _guarded("proactive_beat", _check_proactive_beat),
        _guarded("proactive_schedules", lambda: _check_proactive_schedules(enabled, now)),
        _guarded("proactive_last_run", lambda: _check_proactive_last_run(enabled, latest, now)),
        _guarded("proactive_digest", _check_proactive_digest),
        _guarded("proactive_probes", lambda: _check_proactive_probes(latest)),
    ]


def _check_n8n_allowlist(manifest):
    """D10: the relay's `BRIDGE_ALLOWED_TOOLS` (`N8N_RELAY_ALLOWED_TOOLS`)
    equals the manifest's server tools -- the second ceiling lists exactly
    the first one's workflows."""
    from connectors.manifests.n8n import relay_allowed_tools

    expected = set(relay_allowed_tools().split(","))
    registered = {policy.server_tool for policy in manifest.tools}
    if registered != expected:
        return CheckResult(
            "n8n_allowlist", FAIL, "the registered n8n manifest differs from the WORKFLOWS table"
        )
    configured = n8n_relay_allowed_tools_setting()
    if not configured:
        return CheckResult(
            "n8n_allowlist", FAIL, "N8N_RELAY_ALLOWED_TOOLS is empty (run n8n_relay_env)"
        )
    extra = sorted(configured - expected)
    missing = sorted(expected - configured)
    if extra or missing:
        parts = []
        if extra:
            parts.append(f"not in the manifest: {', '.join(extra)}")
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        return CheckResult(
            "n8n_allowlist",
            FAIL,
            "N8N_RELAY_ALLOWED_TOOLS differs from the manifest (run n8n_relay_env); "
            + "; ".join(parts),
        )
    return CheckResult(
        "n8n_allowlist", PASS, f"relay ceiling equals the manifest ({len(expected)} workflow(s))"
    )


def _n8n_unreachable(binding_row, relay_row):
    return [
        binding_row,
        relay_row,
        CheckResult("n8n_pins", WARN, "not checked: the relay is not reachable"),
    ]


def _check_n8n_live(manifest):
    """`n8n_binding`, `n8n_relay`, `n8n_pins`: `warn` while no binding is
    enabled (owner step); once one is, the relay must answer a read-only
    `tools/list` (`pin_report`, nothing stored) and every pin must match."""
    from connectors.mcp_client import ConnectorError
    from connectors.models import ConnectorBinding

    bindings = list(
        ConnectorBinding.objects.filter(connector=N8N_CONNECTOR, enabled=True).select_related(
            "workspace"
        )
    )
    if not bindings:
        return [
            CheckResult("n8n_binding", WARN, f"no enabled n8n binding: {N8N_OWNER_STEP}"),
            CheckResult("n8n_relay", WARN, "not checked: no enabled n8n binding"),
            CheckResult("n8n_pins", WARN, "not checked: no enabled n8n binding"),
        ]
    misplaced = [b for b in bindings if b.workspace.type not in manifest.workspace_types]
    if misplaced:
        binding_row = CheckResult(
            "n8n_binding", FAIL, f"{len(misplaced)} enabled binding(s) outside a personal workspace"
        )
    else:
        binding_row = CheckResult(
            "n8n_binding", PASS, f"{len(bindings)} enabled binding(s) in a personal workspace"
        )
    if not os.environ.get(manifest.url_env, "").strip():
        return _n8n_unreachable(
            binding_row,
            CheckResult(
                "n8n_relay", FAIL, f"enabled but {manifest.url_env} is not set (every call refuses)"
            ),
        )
    try:
        statuses = pin_report(manifest, timeout=_timeout())
    except ConnectorError as exc:
        return _n8n_unreachable(
            binding_row,
            CheckResult("n8n_relay", FAIL, f"tools/list through the relay failed: {exc.code}"),
        )
    except Exception as exc:
        return _n8n_unreachable(
            binding_row,
            CheckResult(
                "n8n_relay", FAIL, f"tools/list through the relay failed: {_error_label(exc)}"
            ),
        )
    relay_row = CheckResult("n8n_relay", PASS, "the relay answered tools/list")
    bad = sorted(f"{tool}={state}" for tool, state in statuses.items() if state != "ok")
    if bad:
        pins_row = CheckResult("n8n_pins", FAIL, "pins differ: " + ", ".join(bad))
    else:
        pins_row = CheckResult("n8n_pins", PASS, f"{len(statuses)} tool pin(s) match")
    return [binding_row, relay_row, pins_row]


def check_n8n():
    """Phase 12 D9-D12, read-only (no tool call, nothing stored on a
    binding): `n8n_allowlist`, `n8n_binding`, `n8n_relay`, `n8n_pins`. An
    unconfigured n8n is a `warn` (owner step), never a `fail`. Details carry
    tool names, setting names and closed codes -- never a URL or the bearer."""
    from connectors.manifest import get_manifest

    manifest = get_manifest(N8N_CONNECTOR)
    if manifest is None or manifest.blocked:
        return [CheckResult("n8n_allowlist", FAIL, "the n8n manifest is not registered")]
    return [
        _guarded("n8n_allowlist", lambda: _check_n8n_allowlist(manifest)),
        *_guarded_rows("n8n_binding", lambda: _check_n8n_live(manifest)),
    ]


def _check_skills_ai():
    return _seeded_row("skills_ai", SKILLS_PURPOSE_PROMPTS)


def _check_skills_beat():
    problems = _worker_beat_problems(SKILLS_BEAT_TASKS)
    if problems:
        return CheckResult("skills_beat", FAIL, "; ".join(problems))
    return CheckResult(
        "skills_beat", PASS, "the weekly curator and the nightly playbook distiller are scheduled"
    )


def _check_skills_parser():
    """D13: every usable (active or stale) skill's current version still
    passes the current SKILL.md parser -- a parser tightening surfaces old
    versions here, by skill name and closed code, before a turn meets them."""
    from skills.models import InstructionSkill
    from skills.skill_md import SkillParseError, parse_skill_md, render_skill_md

    usable = list(
        InstructionSkill.objects.filter(
            status__in=[InstructionSkill.Status.ACTIVE, InstructionSkill.Status.STALE]
        )
        .select_related("current_version")
        .order_by("workspace_id", "name")
    )
    if not usable:
        return CheckResult("skills_parser", PASS, "no active skill")
    refused = []
    for skill in usable:
        version = skill.current_version
        if version is None:
            refused.append(f"{skill.name}=no_version")
            continue
        try:
            parse_skill_md(
                render_skill_md(version.front_matter or {}, version.body or ""),
                references=version.references or {},
            )
        except SkillParseError as exc:
            refused.append(f"{skill.name}={exc.code}")
    if refused:
        return CheckResult(
            "skills_parser",
            FAIL,
            f"{len(refused)} of {len(usable)} usable skill(s) fail the current parser "
            f"(archive or re-approve): {', '.join(refused)}",
        )
    return CheckResult(
        "skills_parser", PASS, f"{len(usable)} usable skill(s) pass the current parser"
    )


def check_skills():
    """Phase 12 D13-D18, read-only (no model call): `skills_ai` (the three
    purposes' tiers, budgets and prompts), `skills_beat` (curator and
    distiller scheduled) and `skills_parser`. Details carry purposes, budget
    names, skill names and closed codes -- never a body."""
    return [
        _guarded("skills_ai", _check_skills_ai),
        _guarded("skills_beat", _check_skills_beat),
        _guarded("skills_parser", _check_skills_parser),
    ]


# --- Phase 13: the home server (branch 7, D14/D15) ---------------------------

# D15 `home_lan_only`: the only address ranges a home-only Friday may answer
# on -- loopback, RFC 1918, IPv6 ULA and the CGNAT range Tailscale uses. A
# fixed list, deliberately not `DEVICE_PAIRING_NETWORKS` (an owner setting).
HOME_LAN_NETWORKS = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
    "100.64.0.0/10",
)
# The status-file formats `deploy/home/friday-backup.sh` and
# `friday-restore-drill.sh` write (D14, pinned).
HOME_STATUS_VERSION = 1
HOME_STATUS_MAX_BYTES = 4096
# A status timestamp this far in the future is a clock or forgery problem.
HOME_STATUS_FUTURE_SKEW_SECONDS = 300
_HOME_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_HOME_DUMP_NAME = re.compile(r"friday-\d{8}T\d{6}Z\.dump\.age")
_HOME_SNAPSHOT = re.compile(r"[0-9a-f]{8,64}")
_HOME_SHA256 = re.compile(r"[0-9a-f]{64}")
HOME_DRILL_RESULTS = ("pass", "fail")
# Phase 13b D1: the host platforms the owner may declare
# (`HOME_SERVER_PLATFORM`); `macos` is the interim server (ADR-017
# amendment 1).
HOME_PLATFORM_LINUX = "linux"
HOME_PLATFORM_MACOS = "macos"
HOME_SERVER_PLATFORMS = (HOME_PLATFORM_LINUX, HOME_PLATFORM_MACOS)
# Phase 13b D2: a restic repository identifier as `friday-backup.sh`
# records it (the same character set the script accepts). Userinfo (`@`)
# and a query (`?`) are refused separately, before this, and never echoed.
_HOME_REPOSITORY = re.compile(r"[A-Za-z0-9._~:/%+=,-]{1,512}")
HOME_R2_HOST_SUFFIX = ".r2.cloudflarestorage.com"


def resolve_host(host):
    """The IP addresses `host` resolves to here (`getaddrinfo`, any family).
    Resolver errors propagate (`OSError`); the caller decides."""
    import socket

    infos = socket.getaddrinfo(host, None)
    return sorted({info[4][0] for info in infos})


class _HomeStatusError(Exception):
    """A configured status file that is missing or malformed: the row fails
    with this message (setting names and field names only, never a path)."""


def _ip_or_none(text):
    import ipaddress

    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _is_lan_address(text):
    import ipaddress

    address = _ip_or_none(text)
    if address is None:
        return False
    return any(
        address.version == network.version and address in network
        for network in map(ipaddress.ip_network, HOME_LAN_NETWORKS)
    )


def _classify_home_host(host):
    """`(verdict, note)` for one host: verdict `ok`, `public`, `wildcard` or
    `unresolved`; note `mdns` for an unresolved `.local` name."""
    host = host.strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or host == "*" or host.startswith("."):
        return "wildcard", ""
    if _ip_or_none(host) is not None:
        return ("ok", "") if _is_lan_address(host) else ("public", "")
    try:
        addresses = resolve_host(host)
    except OSError:
        addresses = []
    if not addresses:
        # mDNS names are link-local by definition (RFC 6762) and do not
        # resolve inside a container: LAN-only without an address to check.
        if host.endswith(".local"):
            return "ok", "mdns"
        return "unresolved", ""
    if all(_is_lan_address(address) for address in addresses):
        return "ok", ""
    return "public", ""


def _check_home_lan_only():
    """Every `DJANGO_ALLOWED_HOSTS` entry and the `PREFLIGHT_NODE_TLS_URL`
    host name only LAN addresses (`HOME_LAN_NETWORKS`). A wildcard, an empty
    host list or a public address fails; an unset URL warns (dev)."""
    name = "home_lan_only"
    hosts = [h for h in (getattr(settings, "ALLOWED_HOSTS", None) or []) if h.strip()]
    if not hosts:
        return CheckResult(name, FAIL, "DJANGO_ALLOWED_HOSTS is empty")
    url = getattr(settings, "PREFLIGHT_NODE_TLS_URL", "")
    checked = [("DJANGO_ALLOWED_HOSTS", host.strip()) for host in hosts]
    if url:
        url_host = urlparse(url).hostname
        if not url_host:
            return CheckResult(name, FAIL, "PREFLIGHT_NODE_TLS_URL has no host")
        checked.append(("PREFLIGHT_NODE_TLS_URL", url_host))
    found = {"public": [], "wildcard": [], "unresolved": [], "mdns": []}
    for source, host in checked:
        verdict, note = _classify_home_host(host)
        label = f"{host} ({source})"
        if verdict != "ok":
            found[verdict].append(label)
        elif note:
            found[note].append(label)
    problems = []
    if found["wildcard"]:
        problems.append("a wildcard host: " + ", ".join(found["wildcard"]))
    if found["public"]:
        problems.append("not a LAN address: " + ", ".join(found["public"]))
    if problems:
        return CheckResult(
            name, FAIL, "; ".join(problems) + " (Friday is home-only, ADR-017 decision 3)"
        )
    summary = f"{len(checked)} host(s) LAN-only"
    if found["mdns"]:
        summary += f" ({len(found['mdns'])} mDNS name(s) taken as link-local)"
    if found["unresolved"]:
        return CheckResult(
            name,
            WARN,
            f"{summary}; could not resolve here: "
            + ", ".join(found["unresolved"])
            + " (not verified LAN-only: a name this container cannot resolve may still"
            " be public; check it resolves only to a LAN address)",
        )
    if not url:
        return CheckResult(name, WARN, f"{summary}; PREFLIGHT_NODE_TLS_URL not set (dev)")
    return CheckResult(name, PASS, summary)


def _check_home_tls_pin():
    """D15: the LAN URL serves `NODE_TLS_PIN_SHA256` -- `node_tls_pin`'s
    comparison under the home row's name."""
    return _check_node_tls_pin(name="home_tls_pin")


def _read_home_status(setting):
    """The parsed JSON object of the status file `setting` names, or None
    when the setting is empty. A missing, oversized or non-JSON file raises
    `_HomeStatusError`."""
    path = getattr(settings, setting, "")
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read(HOME_STATUS_MAX_BYTES + 1)
    except FileNotFoundError as exc:
        raise _HomeStatusError(f"no status file at {setting} (run the script once)") from exc
    except OSError as exc:
        raise _HomeStatusError(f"{setting} is not readable ({type(exc).__name__})") from exc
    if len(raw) > HOME_STATUS_MAX_BYTES:
        raise _HomeStatusError(f"the {setting} file is larger than {HOME_STATUS_MAX_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _HomeStatusError(f"the {setting} file is not JSON") from exc
    if not isinstance(data, dict):
        raise _HomeStatusError(f"the {setting} file is not a JSON object")
    version = data.get("v")
    if isinstance(version, bool) or version != HOME_STATUS_VERSION:
        raise _HomeStatusError(f"the {setting} file is not format v{HOME_STATUS_VERSION}")
    return data


def _home_timestamp(data, field, setting, now):
    from datetime import UTC, datetime

    value = data.get(field)
    error = _HomeStatusError(f"{field} in the {setting} file is not a UTC timestamp")
    if not isinstance(value, str) or not _HOME_TIMESTAMP.fullmatch(value):
        raise error
    try:
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise error from exc
    if (moment - now).total_seconds() > HOME_STATUS_FUTURE_SKEW_SECONDS:
        raise _HomeStatusError(f"{field} in the {setting} file is in the future")
    return moment


def _home_age_label(seconds):
    seconds = max(0, int(seconds))
    if seconds < 2 * 3600:
        return f"{seconds // 60} min"
    if seconds < 3 * 24 * 3600:
        return f"{seconds // 3600} h"
    return f"{seconds // (24 * 3600)} d"


def _home_thresholds(warn_setting, fail_setting, unit_seconds):
    warn = _int_setting(warn_setting)
    fail = _int_setting(fail_setting)
    if warn is None or fail is None or warn <= 0 or fail <= warn:
        raise _HomeStatusError(f"{fail_setting} must be greater than {warn_setting} (> 0)")
    return warn * unit_seconds, fail * unit_seconds


def _home_dump_name(data, setting, *, allow_empty=False):
    dump = data.get("dump")
    if isinstance(dump, str) and (_HOME_DUMP_NAME.fullmatch(dump) or (allow_empty and not dump)):
        return dump
    raise _HomeStatusError(f"dump in the {setting} file is not a backup file name")


def _home_repository_refusal(value):
    """Why a restic repository identifier cannot be used, or None. Never
    contains the value itself: a refused value may carry a credential."""
    if "@" in value:
        return "carries userinfo (@)"
    if "?" in value:
        return "carries a query (?)"
    if not _HOME_REPOSITORY.fullmatch(value):
        return "is not a restic repository identifier"
    return None


def _home_repository_host(value):
    """The host of a URL-shaped repository (`s3:https://host/bucket`,
    `rest:https://host/`), else None."""
    _backend, sep, rest = value.partition(":")
    if sep and "://" in rest:
        return (urlparse(rest).hostname or "").lower() or None
    return None


def home_repository_label(value):
    """A short, non-secret description of a repository: `<backend>:<scheme>
    ://<host>` for a URL-shaped one, `<backend>:<host>` for another remote
    backend, `a local path` for a directory. Never a path or a bucket."""
    backend, sep, rest = value.partition(":")
    if not sep or not backend or "/" in backend or backend.startswith("."):
        return "a local path"
    if "://" in rest:
        parsed = urlparse(rest)
        return f"{backend}:{parsed.scheme}://{(parsed.hostname or '').lower()}"
    head = re.split(r"[/:]", rest, maxsplit=1)[0]
    return f"{backend}:{head}" if head else backend


def _home_is_r2(value):
    host = _home_repository_host(value)
    return bool(host) and host.endswith(HOME_R2_HOST_SUFFIX)


def _home_offsite_repository():
    """The configured `HOME_BACKUP_OFFSITE_REPOSITORY` (stripped; trailing
    slashes are not significant), or "" when unset. A value with userinfo
    or a query raises `_HomeStatusError` without echoing it."""
    value = (getattr(settings, "HOME_BACKUP_OFFSITE_REPOSITORY", "") or "").strip()
    if not value:
        return ""
    refusal = _home_repository_refusal(value)
    if refusal is not None:
        raise _HomeStatusError(
            f"HOME_BACKUP_OFFSITE_REPOSITORY {refusal}: set only the non-secret"
            " repository identifier (no key, no userinfo, no query)"
        )
    return value.rstrip("/")


def _home_recorded_repository(data, setting):
    """The `repository` field of `status.json` (Phase 13b D2, optional), or
    None when the file predates it. A malformed value or one with userinfo
    or a query raises `_HomeStatusError` without echoing it."""
    if "repository" not in data:
        return None
    value = data["repository"]
    if not isinstance(value, str) or not value:
        raise _HomeStatusError(f"repository in the {setting} file is not a repository")
    refusal = _home_repository_refusal(value)
    if refusal is not None:
        raise _HomeStatusError(
            f"repository in the {setting} file {refusal} (friday-backup.sh never records"
            " that: check the script and the host's restic environment file)"
        )
    return value.rstrip("/")


def _check_home_backup_fresh():
    """`status.json` from `deploy/home/friday-backup.sh` (D14): well-formed,
    finished at most `HOME_BACKUP_WARN_HOURS` ago (warn up to
    `HOME_BACKUP_FAIL_HOURS`, then fail). Reads the status file only.

    Phase 13b D2: with `HOME_BACKUP_OFFSITE_REPOSITORY` set, the last
    backup must also have recorded that repository -- absent is a warning
    (an old script), a different one a failure; the detail names R2 when
    the host is Cloudflare R2's. No call to the bucket: the web container
    holds no bucket credential (ADR-017 decision 6)."""
    from django.utils import timezone

    name = "home_backup_fresh"
    setting = "HOME_BACKUP_STATUS_PATH"
    try:
        warn_after, fail_after = _home_thresholds(
            "HOME_BACKUP_WARN_HOURS", "HOME_BACKUP_FAIL_HOURS", 3600
        )
        offsite = _home_offsite_repository()
        data = _read_home_status(setting)
        if data is None:
            return CheckResult(name, WARN, f"{setting} not set (owner step: runbook step 8)")
        now = timezone.now()
        finished = _home_timestamp(data, "finished_at", setting, now)
        dump = _home_dump_name(data, setting)
        size = data.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise _HomeStatusError(f"bytes in the {setting} file is not a positive integer")
        digest = data.get("sha256")
        if not isinstance(digest, str) or not _HOME_SHA256.fullmatch(digest):
            raise _HomeStatusError(f"sha256 in the {setting} file is not a sha256 hex digest")
        snapshot = data.get("restic_snapshot")
        if not isinstance(snapshot, str) or not _HOME_SNAPSHOT.fullmatch(snapshot):
            raise _HomeStatusError(f"restic_snapshot in the {setting} file is not a snapshot id")
        recorded = _home_recorded_repository(data, setting)
    except _HomeStatusError as exc:
        return CheckResult(name, FAIL, str(exc))
    age = (now - finished).total_seconds()
    detail = f"last backup {_home_age_label(age)} ago ({dump}, restic {snapshot})"
    if offsite and recorded is not None and recorded != offsite:
        return CheckResult(
            name,
            FAIL,
            f"{detail}; the last backup went to {home_repository_label(recorded)}, not the"
            " configured off-site repository (HOME_BACKUP_OFFSITE_REPOSITORY; check the"
            " host's restic environment file)",
        )
    notes = []
    if offsite and recorded is None:
        notes.append(
            "the last backup did not record its repository -- update friday-backup.sh"
            " (the off-site destination is not verified)"
        )
    elif offsite:
        where = "R2" if _home_is_r2(offsite) else home_repository_label(offsite)
        detail += f", off-site {where}"
    if age > fail_after:
        return CheckResult(
            name,
            FAIL,
            f"{detail}; older than HOME_BACKUP_FAIL_HOURS (is friday-backup.timer on? on the"
            " interim Mac: the LaunchAgent)",
        )
    if age > warn_after:
        notes.insert(0, "older than HOME_BACKUP_WARN_HOURS")
    return CheckResult(name, WARN if notes else PASS, "; ".join([detail, *notes]))


def _check_home_restore_drill_age():
    """`drill.json` from `deploy/home/friday-restore-drill.sh` (D14): the
    last drill passed at most `HOME_DRILL_WARN_DAYS` ago (warn up to
    `HOME_DRILL_FAIL_DAYS`, then fail); a failed last drill fails."""
    from django.utils import timezone

    name = "home_restore_drill_age"
    setting = "HOME_RESTORE_DRILL_STATUS_PATH"
    try:
        warn_after, fail_after = _home_thresholds(
            "HOME_DRILL_WARN_DAYS", "HOME_DRILL_FAIL_DAYS", 24 * 3600
        )
        data = _read_home_status(setting)
        if data is None:
            return CheckResult(name, WARN, f"{setting} not set (owner step: runbook step 9)")
        now = timezone.now()
        drilled = _home_timestamp(data, "drilled_at", setting, now)
        result = data.get("result")
        if result not in HOME_DRILL_RESULTS:
            raise _HomeStatusError(f"result in the {setting} file is not pass or fail")
        dump = _home_dump_name(data, setting, allow_empty=result == "fail")
    except _HomeStatusError as exc:
        return CheckResult(name, FAIL, str(exc))
    age = (now - drilled).total_seconds()
    label = f"{_home_age_label(age)} ago"
    if result == "fail":
        return CheckResult(
            name,
            FAIL,
            f"the last restore drill failed {label} ({dump or 'no dump'}): "
            "fix the backup and drill again",
        )
    detail = f"last restore drill passed {label} ({dump})"
    if age <= warn_after:
        return CheckResult(name, PASS, detail)
    if age <= fail_after:
        return CheckResult(name, WARN, f"{detail}; older than HOME_DRILL_WARN_DAYS")
    return CheckResult(name, FAIL, f"{detail}; older than HOME_DRILL_FAIL_DAYS")


def _home_platform():
    """The declared `HOME_SERVER_PLATFORM` (lower-cased; empty = `linux`),
    or None when it is not one of `HOME_SERVER_PLATFORMS`."""
    value = (getattr(settings, "HOME_SERVER_PLATFORM", "") or "").strip().lower()
    value = value or HOME_PLATFORM_LINUX
    return value if value in HOME_SERVER_PLATFORMS else None


def _check_home_server_platform():
    """Phase 13b D1: the platform the owner declares. `linux` (the home PC)
    passes; `macos` (the interim server on the MacBook, ADR-017 amendment
    1) is a warning, because Friday is down whenever the Mac sleeps; any
    other value fails (the value is not echoed)."""
    name = "home_server_platform"
    platform = _home_platform()
    if platform == HOME_PLATFORM_LINUX:
        return CheckResult(name, PASS, "linux (the always-on home server)")
    if platform == HOME_PLATFORM_MACOS:
        return CheckResult(
            name,
            WARN,
            "macos: interim server (ADR-017 amendment 1): Friday is down while the Mac sleeps",
        )
    return CheckResult(
        name, FAIL, "HOME_SERVER_PLATFORM is not one of " + ", ".join(HOME_SERVER_PLATFORMS)
    )


def _check_home_disk_encryption_macos(value):
    """Phase 13b D1: on the interim Mac, `HOME_FDE_CONFIRMED_ON` is the
    FileVault attestation -- always a warning (never pass, never fail)."""
    from datetime import date

    from django.utils import timezone

    name = "home_disk_encryption"
    suffix = "no LUKS/TPM on macOS (interim)"
    confirmed = None
    if value:
        try:
            confirmed = date.fromisoformat(value)
        except ValueError:
            confirmed = None
        if confirmed is not None and confirmed > timezone.now().date():
            confirmed = None
    if confirmed is not None:
        return CheckResult(name, WARN, f"FileVault owner-confirmed on {confirmed}; {suffix}")
    reason = (
        "HOME_FDE_CONFIRMED_ON not set"
        if not value
        else "HOME_FDE_CONFIRMED_ON is not a past ISO date (YYYY-MM-DD)"
    )
    return CheckResult(
        name,
        WARN,
        f"FileVault not confirmed ({reason}; owner attestation, runbook I1); {suffix}",
    )


def _check_home_disk_encryption():
    """`HOME_FDE_CONFIRMED_ON`: the owner's attestation that the server disk
    is LUKS encrypted (a container cannot see the host's disks). Never a
    fail: unset or unusable is a warning.

    Phase 13b D1: on `HOME_SERVER_PLATFORM=macos` the same setting is the
    FileVault attestation and the row is ALWAYS a warning: there is no
    LUKS/TPM on the interim Mac, and an attestation a container cannot
    verify is not reported as verified. An unknown platform (which
    `home_server_platform` fails) is a warning here."""
    from datetime import date

    from django.utils import timezone

    name = "home_disk_encryption"
    value = (getattr(settings, "HOME_FDE_CONFIRMED_ON", "") or "").strip()
    platform = _home_platform()
    if platform is None:
        return CheckResult(
            name, WARN, "HOME_SERVER_PLATFORM is unknown: disk encryption not assessed"
        )
    if platform == HOME_PLATFORM_MACOS:
        return _check_home_disk_encryption_macos(value)
    if not value:
        return CheckResult(
            name, WARN, "HOME_FDE_CONFIRMED_ON not set (owner attestation, runbook step 1)"
        )
    try:
        confirmed = date.fromisoformat(value)
    except ValueError:
        return CheckResult(name, WARN, "HOME_FDE_CONFIRMED_ON is not an ISO date (YYYY-MM-DD)")
    if confirmed > timezone.now().date():
        return CheckResult(name, WARN, "HOME_FDE_CONFIRMED_ON is in the future")
    return CheckResult(name, PASS, f"owner confirmed full-disk encryption on {confirmed}")


def check_home():
    """Phase 13 D15 (branch 7), read-only: `home_lan_only`, `home_tls_pin`,
    `home_backup_fresh`, `home_restore_drill_age`, `home_disk_encryption`,
    and (Phase 13b D1) `home_server_platform`.
    Nothing is sent to a node, no model is called, no backup is started or
    read -- only the two status files the host's scripts write. Details
    carry host names, setting names, file names and ages -- never a path or
    a secret."""
    return [
        _guarded("home_lan_only", _check_home_lan_only),
        _guarded("home_tls_pin", _check_home_tls_pin),
        _guarded("home_backup_fresh", _check_home_backup_fresh),
        _guarded("home_restore_drill_age", _check_home_restore_drill_age),
        _guarded("home_disk_encryption", _check_home_disk_encryption),
        _guarded("home_server_platform", _check_home_server_platform),
    ]


# --- Phase 13 closeout: labels, the HA allowlist, the Linux node, no protocol change

# Boundary "no new protocol surface" (`node_protocol_frozen`): Phase 13 adds
# no WS message, schema field or capability contract (ADR-013 and ADR-015
# unchanged, ADR-017). The node protocol surface (schemas, vectors,
# capability contracts, conformance cases and vectors) and
# `node/friday-voice/conformance.lock`, hashed at the Phase 12 closeout
# merge (7d2dee5, `chore/p12-closeout`). A later protocol change updates
# these digests in the same branch as its ADR. Re-pinned at the
# integ/phase-13 integration of the Phase 10 protocol v2 (ADR-013
# "Implementation notes -- protocol v2", `feat/p10-node-confirm`) and the
# runbook A12 move of the reference contracts into `nodes/` (235 files);
# `node/friday-voice/conformance.lock` is unchanged.
NODE_PROTOCOL_GLOBS = (
    "nodes/protocol/schemas/**/*.json",
    "nodes/protocol/vectors/**/*.json",
    "nodes/capabilities/**/*.json",
    "nodes/conformance/cases/**/*.json",
    "nodes/conformance/vectors/**/*.json",
)
PHASE12_NODE_PROTOCOL_SHA256 = "543be31274e000cb10c56d5ec7249b1344cfaff4cb55e74eb3abf41906669c51"
PHASE12_NODE_PROTOCOL_FILES = 235
PHASE12_FRIDAY_VOICE_LOCK_SHA256 = (
    "a9be6508206d1d779036119ddaf3ef2453f2333bb46166a6d9f093c0eae1ef29"
)
# D15 `linux_node_vendor`: the trees `node/friday-node-linux/scripts/
# vendor.py` vendors whole (a test pins that both lists agree; the backend
# imports nothing from the package, this check reads hashes only).
LINUX_NODE_LOCK = "vendor.lock"
LINUX_NODE_VENDOR = "friday_node_linux/_vendor"
LINUX_NODE_VENDORED_TREES = (
    "nodes/protocol",
    "nodes/capabilities",
    "nodes/reference/fixtures",
)
_VENDOR_LOCK_LINE = re.compile(r"^([0-9a-f]{64})  ([0-9a-f]{64})  ([A-Za-z0-9_./-]+)$")
_VENDOR_SKIP_PARTS = frozenset({"__pycache__", ".pytest_cache"})
HA_OWNER_STEP = "owner step 5: allowlist entities on /connectors/home-assistant"


def node_protocol_digest(backend=None):
    """`(sha256, file count)` of the node protocol surface under `backend`
    (default: this checkout's backend): sha256 over the sorted
    `<sha256 of file>  <path>` lines of every `NODE_PROTOCOL_GLOBS` file."""
    import hashlib

    backend = Path(backend) if backend is not None else Path(settings.BASE_DIR).resolve()
    files = sorted(
        {
            path.relative_to(backend).as_posix()
            for pattern in NODE_PROTOCOL_GLOBS
            for path in backend.glob(pattern)
            if path.is_file()
        }
    )
    digest = hashlib.sha256()
    for relative in files:
        digest.update(f"{_sha256_file(backend / relative)}  {relative}\n".encode())
    return digest.hexdigest(), len(files)


def linux_node_dir():
    """The `node/friday-node-linux` checkout preflight hashes, or None when
    this host has none (the backend image does not ship `node/`)."""
    configured = getattr(settings, "PREFLIGHT_LINUX_NODE_DIR", "") or ""
    path = (
        Path(configured)
        if configured
        else Path(settings.BASE_DIR).resolve().parent / "node" / "friday-node-linux"
    )
    return path if path.is_dir() else None


def ha_state_ids():
    """The entity ids of one read-only `ha_states` read (the bridge's
    `get_states` under the manifest's own pin, every domain the deployment
    may read). Raises the bridge's `ConnectorError` (`not_configured`
    without `HA_BASE_URL` / `HA_TOKEN`)."""
    from connectors.bridges import home_assistant as bridge
    from connectors.manifest import get_manifest

    manifest = get_manifest("home_assistant")
    (policy,) = [p for p in manifest.tools if p.server_tool == bridge.SERVER_TOOL_STATES]
    result = bridge.call_tool(
        manifest,
        server_tool=bridge.SERVER_TOOL_STATES,
        arguments={},
        schema_sha256=policy.schema_sha256,
        timeout=_timeout(),
    )
    return {entity["entity_id"] for entity in (result.structured or {}).get("entities", [])}


def _label_list(values, limit=5):
    values = sorted(values)
    shown = ", ".join(values[:limit])
    return shown + (f" (+{len(values) - limit} more)" if len(values) > limit else "")


def _check_nodes_labels():
    """D3/D15: per owner, every non-revoked node has a unique name (its
    `name_key`), and rooms are set once the owner has more than one node --
    what "which PC" resolves on. A node without a room in a multi-node home
    is a `warn`; a duplicate `name_key` (pairing and `PATCH` prevent it) a
    `fail`. Details carry node names and counts only."""
    name = "nodes_labels"
    active = _active_nodes()
    if not active:
        return CheckResult(name, WARN, "no node paired (pending owner evaluation, M2)")
    by_owner = {}
    for node, _status in active:
        by_owner.setdefault(node.owner_id, []).append(node)
    duplicates, roomless = [], []
    for nodes in by_owner.values():
        keys = Counter(node.name_key or node.name for node in nodes)
        duplicates += [n.name for n in nodes if keys[n.name_key or n.name] > 1]
        if len(nodes) > 1:
            roomless += [n.name for n in nodes if n.room_id is None]
    detail = f"{len(active)} node(s) of {len(by_owner)} owner(s)"
    if duplicates:
        return CheckResult(
            name,
            FAIL,
            f"{detail}; duplicate names: {_label_list(duplicates)} "
            "(rename on /devices: 'which PC' cannot tell them apart)",
        )
    if roomless:
        return CheckResult(
            name, WARN, f"{detail}; no room set: {_label_list(roomless)} (set it on /devices)"
        )
    return CheckResult(name, PASS, f"{detail}, names unique, rooms set")


def _check_ha_entities():
    """D11/D15: the owner's entity allowlist. Every enabled `HomeEntity`
    stays inside the bridge's code maximum (never a `NEVER_DOMAINS` or
    read-only domain, services a subset of `SERVICES`) -- else `fail` --
    and still exists in Home Assistant (one read-only `ha_states` read,
    seam `ha_state_ids`). None allowlisted, or HA not configured, is a
    `warn`: no entity call is possible then (fail closed). Details carry
    entity ids, codes and counts -- never a state, a token or a URL."""
    from connectors.bridges import home_assistant as bridge
    from connectors.mcp_client import ConnectorError
    from connectors.models import HomeEntity

    name = "ha_entities"
    rows = list(HomeEntity.objects.filter(enabled=True).order_by("entity_id"))
    if not rows:
        return CheckResult(name, WARN, f"no entity allowlisted ({HA_OWNER_STEP})")
    outside = [
        f"{row.entity_id}={refusal}"
        for row in rows
        if (refusal := bridge.code_maximum_refusal(row.entity_id, row.services)) is not None
    ]
    if outside:
        return CheckResult(
            name, FAIL, f"outside the code maximum: {_label_list(outside)} (remove them)"
        )
    readable = bridge.read_domains()
    narrowed = [r.entity_id for r in rows if bridge.entity_domain(r.entity_id) not in readable]
    checked = [r.entity_id for r in rows if r.entity_id not in narrowed]
    detail = f"{len(rows)} enabled entit{'y' if len(rows) == 1 else 'ies'}"
    notes = []
    if narrowed:
        notes.append(f"narrowed away by HA_ALLOWED_DOMAINS: {_label_list(narrowed)}")
    if checked:
        try:
            present = ha_state_ids()
        except ConnectorError as exc:
            status = WARN if exc.code == "not_configured" else FAIL
            return CheckResult(name, status, f"{detail}; ha_states read failed: {exc.code}")
        missing = [entity_id for entity_id in checked if entity_id not in present]
        if missing:
            notes.append(f"not found in Home Assistant: {_label_list(missing)}")
    if notes:
        return CheckResult(name, WARN, f"{detail}; " + "; ".join(notes))
    return CheckResult(name, PASS, f"{detail}, each inside the code maximum and present in HA")


def _read_vendor_lock(path):
    """`{relative path: (source sha256, vendored sha256)}`, or None when a
    line is malformed."""
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = _VENDOR_LOCK_LINE.match(line)
        if match is None or ".." in match.group(3).split("/"):
            return None
        entries[match.group(3)] = (match.group(1), match.group(2))
    return entries


def _vendored_tree_files(backend):
    return {
        path.relative_to(backend).as_posix()
        for tree in LINUX_NODE_VENDORED_TREES
        for path in (backend / tree).rglob("*")
        if path.is_file() and not _VENDOR_SKIP_PARTS & set(path.parts) and path.suffix != ".pyc"
    }


def _check_linux_node_vendor():
    """D10/D15: `node/friday-node-linux/vendor.lock` against this backend --
    every locked backend source unchanged, every vendored copy as locked,
    no file of a vendored tree left out. Hashes only (the backend imports
    nothing from the package; `scripts/vendor.py --check` is the CI step).
    No checkout here is a `warn`."""
    name = "linux_node_vendor"
    root = linux_node_dir()
    if root is None:
        return CheckResult(name, WARN, "not checkable here (no node/friday-node-linux)")
    lock = root / LINUX_NODE_LOCK
    if not lock.is_file():
        return CheckResult(name, FAIL, f"{LINUX_NODE_LOCK} missing (run scripts/vendor.py)")
    entries = _read_vendor_lock(lock)
    if not entries:
        return CheckResult(name, FAIL, f"{LINUX_NODE_LOCK} is malformed or empty")
    backend = Path(settings.BASE_DIR).resolve()
    vendor = root / LINUX_NODE_VENDOR
    drift = []
    for relative, (source_sha, copy_sha) in entries.items():
        source = backend / relative
        if not source.is_file() or _sha256_file(source) != source_sha:
            drift.append(f"backend/{relative}")
        copy = vendor / relative
        if not copy.is_file() or _sha256_file(copy) != copy_sha:
            drift.append(f"_vendor/{relative}")
    left_out = _vendored_tree_files(backend) - set(entries)
    drift += [f"backend/{relative} (not vendored)" for relative in left_out]
    if drift:
        return CheckResult(
            name,
            FAIL,
            f"drift against {LINUX_NODE_LOCK}: {_label_list(drift)} (run scripts/vendor.py)",
        )
    return CheckResult(name, PASS, f"{len(entries)} vendored file(s) match {LINUX_NODE_LOCK}")


def _check_node_protocol_frozen():
    """Boundary "no new protocol surface": the node protocol files and the
    `friday-voice` conformance lock hash to the Phase 12 closeout values
    (`node_conformance` and `friday_voice_conformance` prove the files are
    current; this row proves they did not move). Without a
    `node/friday-voice` checkout its half is skipped (a `warn`)."""
    name = "node_protocol_frozen"
    digest, count = node_protocol_digest()
    problems = []
    if digest != PHASE12_NODE_PROTOCOL_SHA256:
        problems.append(
            f"the node protocol surface changed ({count} files, was "
            f"{PHASE12_NODE_PROTOCOL_FILES}; sha256 {digest[:12]})"
        )
    root = friday_voice_dir()
    if root is not None:
        lock = root / FRIDAY_VOICE_LOCK
        if not lock.is_file():
            problems.append(f"node/friday-voice/{FRIDAY_VOICE_LOCK} missing")
        elif _sha256_file(lock) != PHASE12_FRIDAY_VOICE_LOCK_SHA256:
            problems.append(f"node/friday-voice/{FRIDAY_VOICE_LOCK} changed")
    if problems:
        return CheckResult(
            name,
            FAIL,
            "; ".join(problems) + " since the Phase 12 closeout (a protocol change needs an ADR)",
        )
    if root is None:
        return CheckResult(
            name,
            WARN,
            f"{count} node protocol files unchanged since the Phase 12 closeout; "
            "friday-voice lock not checkable here (no node/friday-voice)",
        )
    return CheckResult(
        name,
        PASS,
        f"{count} node protocol files and the friday-voice lock unchanged since the "
        "Phase 12 closeout",
    )


def check_home_control():
    """Phase 13 closeout (D15), read-only: `nodes_labels`, `ha_entities`,
    `linux_node_vendor` and `node_protocol_frozen`. One `ha_states` read is
    the only outside contact; nothing is sent to a node, no model is called
    and nothing is written."""
    return [
        _guarded("nodes_labels", _check_nodes_labels),
        _guarded("ha_entities", _check_ha_entities),
        _guarded("linux_node_vendor", _check_linux_node_vendor),
        _guarded("node_protocol_frozen", _check_node_protocol_frozen),
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
    ("graph", check_graph),
    ("nodes", check_nodes),
    ("control", check_control),
    ("live_voice", check_live_voice),
    ("proactive", check_proactive),
    ("n8n", check_n8n),
    ("skills", check_skills),
    ("home", check_home),
    ("home_control", check_home_control),
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
