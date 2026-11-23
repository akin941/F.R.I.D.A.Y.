"""`manage.py preflight` (Phase 5 closeout). Every outside contact goes
through a seam in `ops.preflight`; these tests replace exactly that seam,
so no test reaches a real provider, MCP server, browser or broker. The
provider checks run through the REAL gateway against the in-process fake
provider (or a recording adapter), so the `ModelUsage` contract is
exercised, not assumed."""

import os
import re
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.services import register_user
from ai_core.adapters.base import (
    AdapterError,
    AdapterResult,
    EmbedAdapterResult,
    SynthesizeAdapterResult,
    TranscribeAdapterResult,
)
from ai_core.audio import parse_stt_input, silent_wav
from ai_core.models import ModelProvider, ModelUsage
from connectors.mcp_client import ConnectorError
from connectors.models import ConnectorBinding
from ops import preflight
from ops.preflight import FAIL, PASS, WARN, CheckResult
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

SECRET = "sk-or-SECRET-CANARY-0123456789abcdef"


def _statuses(results):
    return {r.name: r.status for r in results}


@pytest.fixture(autouse=True)
def quiet(settings):
    settings.PREFLIGHT_WEB_URL = ""
    settings.PREFLIGHT_BFF_URL = ""
    settings.PREFLIGHT_FRONTEND_BUILD_ID_FILE = ""
    settings.PREFLIGHT_TIMEOUT_SECONDS = 1
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.PREFLIGHT_VOICE_MUTE_CMD = ""


@pytest.fixture
def pages(monkeypatch):
    """`{url: (status, body)}` served by the `http_get` seam; any other URL
    raises a connection error."""
    served = {}
    requested = []

    def fake_get(url, *, timeout):
        requested.append(url)
        if url not in served:
            raise ConnectionError("refused")
        return served[url]

    monkeypatch.setattr(preflight, "http_get", fake_get)
    served["__requested__"] = requested
    return served


# --- web / bff -------------------------------------------------------------


def test_web_and_bff_warn_when_not_configured():
    assert preflight.check_web()[0].status == WARN
    assert preflight.check_bff()[0].status == WARN


def test_web_pass_fail_and_unreachable(settings, pages):
    settings.PREFLIGHT_WEB_URL = "http://web/healthz"
    pages["http://web/healthz"] = (200, "ok")
    assert preflight.check_web()[0].status == PASS
    pages["http://web/healthz"] = (503, "")
    result = preflight.check_web()[0]
    assert (result.status, result.detail) == (FAIL, "HTTP 503")
    settings.PREFLIGHT_WEB_URL = "http://elsewhere/healthz"
    result = preflight.check_web()[0]
    assert result.status == FAIL
    assert "ConnectionError" in result.detail


def test_bff_pass(settings, pages):
    settings.PREFLIGHT_BFF_URL = "http://bff/"
    pages["http://bff/"] = (200, "<html></html>")
    assert preflight.check_bff()[0].status == PASS


def test_error_detail_never_carries_the_exception_message(settings, monkeypatch):
    settings.PREFLIGHT_WEB_URL = "http://user:pw@web/healthz"

    def boom(url, *, timeout):
        raise RuntimeError(f"cannot reach {url}")

    monkeypatch.setattr(preflight, "http_get", boom)
    detail = preflight.check_web()[0].detail
    assert "pw" not in detail
    assert detail == "unreachable: RuntimeError"


# --- postgres / pgvector / redis / celery ----------------------------------


def test_postgres_and_pgvector_live():
    # The test database is the real Postgres+pgvector the suite runs on.
    assert preflight.check_postgres()[0].status == PASS
    result = preflight.check_pgvector()[0]
    assert result.status == PASS
    assert result.detail.startswith("vector ")


def test_postgres_unreachable(monkeypatch):
    def down(sql):
        raise OSError("connection refused")

    monkeypatch.setattr(preflight, "db_fetchone", down)
    assert preflight.check_postgres()[0].status == FAIL
    assert preflight.check_pgvector()[0].status == FAIL


def test_pgvector_missing(monkeypatch):
    monkeypatch.setattr(preflight, "db_fetchone", lambda sql: None)
    result = preflight.check_pgvector()[0]
    assert (result.status, result.detail) == (FAIL, "extension `vector` not installed")


def test_redis(settings, monkeypatch):
    settings.REDIS_URL = ""
    assert preflight.check_redis()[0].status == WARN
    settings.REDIS_URL = "redis://redis:6379/0"
    monkeypatch.setattr(preflight, "redis_ping", lambda url, *, timeout: True)
    assert preflight.check_redis()[0].status == PASS
    monkeypatch.setattr(preflight, "redis_ping", lambda url, *, timeout: False)
    assert preflight.check_redis()[0].status == FAIL

    def down(url, *, timeout):
        raise ConnectionError("refused")

    monkeypatch.setattr(preflight, "redis_ping", down)
    assert preflight.check_redis()[0].status == FAIL


def test_celery(settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    monkeypatch.setattr(
        preflight, "celery_ping", lambda *, timeout: pytest.fail("eager: nothing to ping")
    )
    assert preflight.check_celery()[0].status == WARN

    settings.CELERY_TASK_ALWAYS_EAGER = False
    monkeypatch.setattr(preflight, "celery_ping", lambda *, timeout: [{"w1": {"ok": "pong"}}])
    result = preflight.check_celery()[0]
    assert (result.status, result.detail) == (PASS, "1 worker(s) answered")
    monkeypatch.setattr(preflight, "celery_ping", lambda *, timeout: [])
    assert preflight.check_celery()[0].status == FAIL

    def broker_down(*, timeout):
        raise OSError("refused")

    monkeypatch.setattr(preflight, "celery_ping", broker_down)
    assert preflight.check_celery()[0].status == FAIL


# --- providers ---------------------------------------------------------------


def _provider(name, *, capability=ModelProvider.Capability.CHAT, key="PREFLIGHT_TEST_KEY", **kw):
    defaults = {
        "provider_type": ModelProvider.ProviderType.OPENAI_COMPATIBLE,
        "model_id": f"{name}-model",
        "api_key_env_var": key,
        "capability": capability,
    }
    if capability == ModelProvider.Capability.EMBEDDING:
        defaults["dimension"] = 3
    defaults.update(kw)
    return ModelProvider.objects.create(name=name, **defaults)


@pytest.fixture
def adapters(monkeypatch):
    """Recording adapters behind the real gateway; `failing` names fail."""
    calls = []
    failing = set()

    class Recording:
        def __init__(self, provider):
            self.provider = provider

        def complete(self, messages, params, *, purpose=""):
            calls.append((self.provider.name, purpose, messages, params))
            if self.provider.name in failing:
                raise AdapterError("auth_error", retryable=False)
            return AdapterResult(text="ok", input_tokens=3, output_tokens=1)

        def embed(self, texts, *, purpose=""):
            calls.append((self.provider.name, purpose, texts, None))
            if self.provider.name in failing:
                raise AdapterError("auth_error", retryable=False)
            return EmbedAdapterResult(vectors=[[0.0, 0.0, 1.0]], input_tokens=1)

    monkeypatch.setattr("ai_core.gateway.get_adapter", lambda p, **kw: Recording(p))
    return calls, failing


def test_no_provider_rows_fails():
    result = preflight.check_providers()
    assert [(r.name, r.status) for r in result] == [("providers", FAIL)]


def test_every_configured_provider_gets_one_pinned_call(settings, monkeypatch, adapters):
    calls, failing = adapters
    settings.AI_FAKE_PROVIDER = False
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "k" * 20)
    monkeypatch.delenv("PREFLIGHT_MISSING_KEY", raising=False)
    _provider("chat-a", priority=1)
    _provider("chat-b", priority=2)  # never reached by normal routing
    _provider("embed-a", capability=ModelProvider.Capability.EMBEDDING)
    _provider("off", is_active=False)
    _provider("unkeyed", key="PREFLIGHT_MISSING_KEY")
    _provider(
        "fake-x", provider_type=ModelProvider.ProviderType.FAKE, key=""
    )  # fake while AI_FAKE_PROVIDER is off

    results = preflight.check_providers()
    statuses = _statuses(results)
    assert statuses == {
        "provider:chat-a": PASS,
        "provider:chat-b": PASS,
        "provider:fake-x": WARN,
        "provider:off": WARN,
        "provider:unkeyed": WARN,
        "provider:embed-a": PASS,
    }
    assert sorted(c[0] for c in calls) == ["chat-a", "chat-b", "embed-a"]
    # Minimal, data-free payloads.
    for _, purpose, payload, _params in calls:
        assert purpose == "ops.preflight"
        assert payload in (preflight.PREFLIGHT_MESSAGES, preflight.PREFLIGHT_EMBED_TEXTS)
    # Single door: one ModelUsage row per call, purpose ops.preflight,
    # pinned (never a fallback).
    rows = ModelUsage.objects.order_by("provider__name")
    assert [(u.provider.name, u.purpose, u.routing_reason, u.success) for u in rows] == [
        ("chat-a", "ops.preflight", "pinned", True),
        ("chat-b", "ops.preflight", "pinned", True),
        ("embed-a", "ops.preflight", "pinned", True),
    ]
    detail = {r.name: r.detail for r in results}
    assert detail["provider:unkeyed"] == "not configured: PREFLIGHT_MISSING_KEY not set"
    assert detail["provider:off"] == "not configured: disabled"
    assert detail["provider:chat-a"] == "model chat-a-model answered"


def test_a_broken_provider_fails_and_is_never_masked_by_a_fallback(settings, monkeypatch, adapters):
    calls, failing = adapters
    settings.AI_FAKE_PROVIDER = False
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "k" * 20)
    _provider("good", priority=1)
    _provider("bad", priority=2)
    failing.add("bad")
    results = {r.name: r for r in preflight.check_providers()}
    assert results["provider:bad"].status == FAIL
    assert results["provider:bad"].detail == "model bad-model: GatewayError (auth_error)"
    assert results["provider:good"].status == PASS
    # The failing call was tried on `bad` only; `good` answered its own.
    assert [c[0] for c in calls].count("bad") == 1
    failed = ModelUsage.objects.get(success=False)
    assert failed.provider.name == "bad"


def test_fake_provider_answers_through_the_real_gateway(settings):
    settings.AI_FAKE_PROVIDER = True
    call_command("seed_ai_defaults", stdout=StringIO())
    results = _statuses(preflight.check_providers())
    assert results["provider:fake-local"] == PASS
    assert results["provider:fake-embedding-local"] == PASS
    assert results["provider:openrouter"] == WARN  # seeded, disabled without a key
    assert set(ModelUsage.objects.values_list("purpose", flat=True)) == {"ops.preflight"}


@pytest.fixture
def speech_adapters(monkeypatch):
    """Recording speech adapters behind the real gateway. `failing` names
    raise; `bad_audio` names return bytes that are not a WAV."""
    calls = []
    failing = set()
    bad_audio = set()

    class Speech:
        def __init__(self, provider):
            self.provider = provider

        def transcribe(self, audio, *, content_type="", params=None, purpose=""):
            calls.append((self.provider.name, purpose, audio, params))
            if self.provider.name in failing:
                raise AdapterError("provider_error", retryable=False)
            return TranscribeAdapterResult(text="(tone)", audio_seconds=1.0)

        def synthesize(self, text, *, params=None, purpose=""):
            calls.append((self.provider.name, purpose, text, params))
            if self.provider.name in failing:
                raise AdapterError("provider_error", retryable=False)
            if self.provider.name in bad_audio:
                return SynthesizeAdapterResult(audio=b"not a wav", audio_seconds=0.0)
            return SynthesizeAdapterResult(
                audio=silent_wav(0.5, sample_rate=22050), audio_seconds=0.5
            )

    monkeypatch.setattr("ai_core.gateway.get_adapter", lambda p, **kw: Speech(p))
    return calls, failing, bad_audio


def test_preflight_wav_is_d3s_upload_shape():
    info = parse_stt_input(preflight.preflight_wav(), max_seconds=30)
    assert (info.sample_rate, info.channels, info.seconds) == (16000, 1, 1.0)
    # A tone, not digital silence: a server that drops silent input still
    # receives something to transcribe.
    assert preflight.preflight_wav() == preflight.preflight_wav()
    assert any(preflight.preflight_wav()[44:200])


def test_speech_rows_get_one_pinned_round_trip_each(settings, monkeypatch, speech_adapters):
    calls, _, _ = speech_adapters
    settings.AI_FAKE_PROVIDER = False
    settings.VOICE_STT_LANGUAGE = "tr"
    settings.VOICE_TTS_ROUTES = {"tr": {"provider": "tts-b", "voice": "tr-voice"}}
    settings.VOICE_TTS_PROVIDER_PARAMS = {}
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "k" * 20)
    _provider("stt-a", capability=ModelProvider.Capability.STT, personal_data_allowed=True)
    _provider("tts-a", capability=ModelProvider.Capability.TTS, personal_data_allowed=True)
    _provider("tts-b", capability=ModelProvider.Capability.TTS, personal_data_allowed=True)

    results = {r.name: r for r in preflight.check_providers()}
    assert {n: r.status for n, r in results.items()} == {
        "provider:stt-a": PASS,
        "provider:tts-a": PASS,
        "provider:tts-b": PASS,
    }
    assert results["provider:stt-a"].detail == "model stt-a-model transcribed the fixture WAV"
    assert results["provider:tts-b"].detail == (
        "model tts-b-model synthesized 0.5s at 22050 Hz (route tr)"
    )
    by_name = {c[0]: c for c in calls}
    assert by_name["stt-a"][2] == preflight.preflight_wav()
    assert by_name["stt-a"][3]["language"] == "tr"
    assert by_name["tts-a"][2] == preflight.PREFLIGHT_TTS_TEXT
    # The routed row is called with its route's voice; the other is not.
    assert by_name["tts-b"][3]["voice"] == "tr-voice"
    assert by_name["tts-a"][3]["voice"] != "tr-voice"
    rows = ModelUsage.objects.order_by("provider__name")
    assert [(u.provider.name, u.purpose, u.routing_reason, u.success) for u in rows] == [
        ("stt-a", "ops.preflight", "pinned", True),
        ("tts-a", "ops.preflight", "pinned", True),
        ("tts-b", "ops.preflight", "pinned", True),
    ]
    # Audio calls are personal data (D5) even for the preflight fixture.
    assert all(u.context_report["personal_data"] for u in rows)


def test_speech_rows_the_gateway_would_refuse_warn_and_are_never_called(
    settings, monkeypatch, speech_adapters
):
    calls, _, _ = speech_adapters
    settings.AI_FAKE_PROVIDER = False
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "k" * 20)
    _provider("stt-cloud", capability=ModelProvider.Capability.STT)  # d2 toggle off
    _provider(
        "tts-off",
        capability=ModelProvider.Capability.TTS,
        is_active=False,
        personal_data_allowed=True,
    )
    results = {r.name: r for r in preflight.check_providers()}
    assert results["provider:stt-cloud"].status == WARN
    assert results["provider:stt-cloud"].detail == (
        "not configured: not personal_data_allowed (d2)"
    )
    assert results["provider:tts-off"].detail == "not configured: disabled"
    assert calls == []
    assert not ModelUsage.objects.exists()


def test_a_broken_speech_row_fails(settings, monkeypatch, speech_adapters):
    _, failing, bad_audio = speech_adapters
    settings.AI_FAKE_PROVIDER = False
    settings.VOICE_TTS_ROUTES = {}
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "k" * 20)
    _provider("stt-a", capability=ModelProvider.Capability.STT, personal_data_allowed=True)
    _provider("tts-a", capability=ModelProvider.Capability.TTS, personal_data_allowed=True)
    _provider("tts-b", capability=ModelProvider.Capability.TTS, personal_data_allowed=True)
    failing.update({"stt-a", "tts-a"})
    bad_audio.add("tts-b")
    results = {r.name: r for r in preflight.check_providers()}
    assert results["provider:stt-a"].status == FAIL
    assert results["provider:stt-a"].detail == "model stt-a-model: GatewayError (provider_error)"
    assert results["provider:tts-a"].status == FAIL
    assert results["provider:tts-b"].status == FAIL
    assert results["provider:tts-b"].detail == "model tts-b-model: unusable audio (not_wav)"


def test_speech_rows_skipped_without_model_calls(settings, monkeypatch, speech_adapters):
    calls, _, _ = speech_adapters
    settings.AI_FAKE_PROVIDER = False
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "k" * 20)
    _provider("stt-a", capability=ModelProvider.Capability.STT, personal_data_allowed=True)
    results = preflight.check_providers(model_calls=False)
    assert results == [CheckResult("provider:stt-a", WARN, "skipped (--no-model-calls)")]
    assert calls == []


def test_seeded_fake_speech_rows_round_trip_through_the_real_gateway(settings):
    settings.AI_FAKE_PROVIDER = True
    call_command("seed_ai_defaults", stdout=StringIO())
    results = {r.name: r for r in preflight.check_providers()}
    assert results["provider:fake-stt-local"].status == PASS
    assert results["provider:fake-tts-local"].status == PASS
    # Seeded disabled and disallowed (A1/d2): never called.
    assert results["provider:openrouter-stt"].status == WARN
    assert results["provider:openrouter-tts"].status == WARN
    called = set(ModelUsage.objects.values_list("provider__name", flat=True))
    assert {"fake-stt-local", "fake-tts-local"} <= called
    assert not called & {"openrouter-stt", "openrouter-tts"}


def test_seeded_stack_has_no_failing_provider_rows(settings):
    settings.AI_FAKE_PROVIDER = True
    call_command("seed_ai_defaults", stdout=StringIO())
    results = preflight.check_providers()
    assert [r.name for r in results if r.status == FAIL] == []


def test_no_model_calls_skips_every_call(settings, monkeypatch, adapters):
    calls, _ = adapters
    settings.AI_FAKE_PROVIDER = False
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "k" * 20)
    _provider("chat-a")
    results = preflight.check_providers(model_calls=False)
    assert [(r.status, r.detail) for r in results] == [(WARN, "skipped (--no-model-calls)")]
    assert calls == []
    assert not ModelUsage.objects.exists()


# --- MCP pins ----------------------------------------------------------------


@pytest.fixture
def workspace():
    user = register_user(email="ops@test.dev", password="Str0ngDevPass!42", name="Ops")
    return Workspace.objects.get(owner=user)


def test_mcp_pins_warn_without_an_enabled_binding(workspace, monkeypatch):
    ConnectorBinding.objects.create(workspace=workspace, connector="github", enabled=False)
    monkeypatch.setattr(
        preflight, "pin_report", lambda m, *, timeout: pytest.fail("nothing to check")
    )
    assert [(r.name, r.status) for r in preflight.check_mcp_pins()] == [("mcp_pins", WARN)]


def test_mcp_pins_ignore_native_connectors(workspace, monkeypatch):
    ConnectorBinding.objects.create(workspace=workspace, connector="home_assistant", enabled=True)
    monkeypatch.setattr(preflight, "pin_report", lambda m, *, timeout: pytest.fail("native"))
    assert preflight.check_mcp_pins()[0].status == WARN


def test_mcp_pins_pass_and_fail(workspace, monkeypatch):
    ConnectorBinding.objects.create(workspace=workspace, connector="github", enabled=True)
    seen = []

    def report(manifest, *, timeout):
        seen.append(manifest.name)
        return {"pull_request_read": "ok", "create_pull_request": "ok"}

    monkeypatch.setattr(preflight, "pin_report", report)
    result = preflight.check_mcp_pins()
    assert [(r.name, r.status) for r in result] == [("mcp_pins:github", PASS)]
    assert seen == ["github"]

    monkeypatch.setattr(
        preflight,
        "pin_report",
        lambda m, *, timeout: {"pull_request_read": "mismatch", "create_pull_request": "missing"},
    )
    result = preflight.check_mcp_pins()[0]
    assert result.status == FAIL
    assert result.detail == "pins differ: create_pull_request=missing, pull_request_read=mismatch"


def test_mcp_pins_server_errors(workspace, monkeypatch):
    ConnectorBinding.objects.create(workspace=workspace, connector="github", enabled=True)

    def raising(code):
        def report(manifest, *, timeout):
            raise ConnectorError(code)

        return report

    monkeypatch.setattr(preflight, "pin_report", raising("not_configured"))
    assert preflight.check_mcp_pins()[0].status == WARN
    monkeypatch.setattr(preflight, "pin_report", raising("timeout"))
    result = preflight.check_mcp_pins()[0]
    assert (result.status, result.detail) == (FAIL, "tools/list failed: timeout")


# --- frontend build / browser secrets / voice --------------------------------


def test_extract_build_id_both_shapes():
    assert preflight.extract_build_id('<script src="/_next/static/abc123/_buildManifest.js">') == (
        "abc123"
    )
    assert preflight.extract_build_id('self.__next_f.push([1,"{\\"b\\":\\"Xy_9-build\\"}"])') == (
        "Xy_9-build"
    )
    assert preflight.extract_build_id("<html></html>") == ""


def test_frontend_build(settings, pages, tmp_path):
    assert preflight.check_frontend_build()[0].status == WARN
    settings.PREFLIGHT_BFF_URL = "http://bff/"
    build_file = tmp_path / "BUILD_ID"
    settings.PREFLIGHT_FRONTEND_BUILD_ID_FILE = str(build_file)
    assert preflight.check_frontend_build()[0].status == FAIL  # file missing
    build_file.write_text("build-1\n")
    pages["http://bff/"] = (200, '<script src="/_next/static/build-1/_ssgManifest.js"></script>')
    assert preflight.check_frontend_build()[0].status == PASS
    build_file.write_text("build-2\n")
    result = preflight.check_frontend_build()[0]
    assert (result.status, result.detail) == (FAIL, "served build-1 != built build-2")
    pages["http://bff/"] = (200, "<html>no id</html>")
    assert preflight.check_frontend_build()[0].status == FAIL


def test_secret_values_picks_secret_names_only(monkeypatch):
    for name in list(os.environ):
        if preflight._SECRET_NAME.search(name):
            monkeypatch.delenv(name)
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.setenv("REDIS_PASSWORD", "short")  # too short to match by chance
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-local")  # documented placeholder
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "x" * 40)  # not a secret name
    assert preflight.secret_values() == {"OPENROUTER_API_KEY": SECRET}


def test_browser_secrets(settings, pages, monkeypatch):
    assert preflight.check_browser_secrets()[0].status == WARN
    settings.PREFLIGHT_BFF_URL = "http://bff/"
    monkeypatch.setattr(preflight, "secret_values", lambda: {"OPENROUTER_API_KEY": SECRET})
    pages["http://bff/"] = (
        200,
        '<script src="/_next/static/chunks/app.js"></script>'
        '<script src="https://cdn.example/other.js"></script>',
    )
    pages["http://bff/_next/static/chunks/app.js"] = (200, "console.log('clean')")
    result = preflight.check_browser_secrets()[0]
    assert (result.status, result.detail) == (PASS, "1 secret(s) absent from 2 served file(s)")
    # Cross-origin scripts are never fetched.
    assert "https://cdn.example/other.js" not in pages["__requested__"]

    pages["http://bff/_next/static/chunks/app.js"] = (200, f"const k='{SECRET}'")
    result = preflight.check_browser_secrets()[0]
    assert result.status == FAIL
    assert result.detail == "reachable from the browser: OPENROUTER_API_KEY"
    assert SECRET not in result.detail


def test_browser_secrets_warn_without_secrets(settings, pages, monkeypatch):
    settings.PREFLIGHT_BFF_URL = "http://bff/"
    monkeypatch.setattr(preflight, "secret_values", dict)
    assert preflight.check_browser_secrets()[0].status == WARN


# --- Phase 6 closeout: voice configuration and the muted-tab browser run ---


def _voice_config():
    return {r.name: r for r in preflight.check_voice_config()}


def test_voice_config_on_a_seeded_fake_stack(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.VOICE_TTS_ROUTES = {"tr": {"provider": "fake-tts-local", "voice": "v"}}
    call_command("seed_ai_defaults", stdout=StringIO())
    results = _voice_config()
    assert results["voice_providers"] == CheckResult(
        "voice_providers", PASS, "stt: fake-stt-local; tts: fake-tts-local"
    )
    assert results["voice_routes"] == CheckResult("voice_routes", PASS, "tr -> fake-tts-local")
    assert results["voice_budget"] == CheckResult(
        "voice_budget", PASS, "audio: monthly 20 USD, session 1 USD"
    )
    # Configuration only: no gateway call.
    assert not ModelUsage.objects.exists()


def test_voice_config_without_speech_rows_fails():
    results = _voice_config()
    assert results["voice_providers"].status == FAIL
    assert results["voice_providers"].detail == ("no STT/TTS provider rows: run seed_ai_defaults")
    assert results["voice_budget"].status == FAIL
    assert results["voice_budget"].detail == (
        "no active budget covers voice.stt, voice.tts: run seed_ai_defaults"
    )


def test_voice_config_warns_when_no_speech_row_is_eligible(settings):
    # The stack as seeded without AI_FAKE_PROVIDER or VOICE_API_KEY: the
    # fake rows are filtered, OpenRouter's are disabled -- voice is simply
    # not configured here, which is a warning, not a failure.
    settings.AI_FAKE_PROVIDER = False
    call_command("seed_ai_defaults", stdout=StringIO())
    settings.VOICE_TTS_ROUTES = {"tr": {"provider": "openrouter-tts"}}
    results = _voice_config()
    assert results["voice_providers"].status == WARN
    assert results["voice_providers"].detail.startswith("no eligible STT/TTS provider")
    assert results["voice_routes"] == CheckResult(
        "voice_routes",
        WARN,
        "routed row not eligible: tr -> openrouter-tts (disabled) (priority fallback)",
    )


def test_voice_config_names_an_enabled_cloud_audio_row(settings, monkeypatch):
    settings.AI_FAKE_PROVIDER = True
    call_command("seed_ai_defaults", stdout=StringIO())
    monkeypatch.setenv("OPENROUTER_API_KEY", "k" * 20)
    # The owner's deliberate d2 toggle.
    ModelProvider.objects.filter(name="openrouter-tts").update(
        is_active=True, personal_data_allowed=True
    )
    detail = _voice_config()["voice_providers"].detail
    assert detail.endswith("; cloud audio enabled: openrouter-tts")


def test_voice_routes_naming_a_missing_row_warn(settings):
    settings.AI_FAKE_PROVIDER = True
    call_command("seed_ai_defaults", stdout=StringIO())
    settings.VOICE_TTS_ROUTES = {
        "en": {"provider": "fake-tts-local"},
        "tr": {"provider": "typo-tts"},
        "de": {},
    }
    assert _voice_config()["voice_routes"] == CheckResult(
        "voice_routes",
        WARN,
        "no TTS row for route: de -> (none), tr -> typo-tts (priority fallback)",
    )
    settings.VOICE_TTS_ROUTES = {}
    assert _voice_config()["voice_routes"].status == WARN


def test_voice_budget_states(settings):
    from ai_core.models import AIBudget

    settings.AI_FAKE_PROVIDER = True
    call_command("seed_ai_defaults", stdout=StringIO())
    AIBudget.objects.filter(name="audio").update(monthly_limit_usd=None)
    result = _voice_config()["voice_budget"]
    assert result.status == WARN
    assert result.detail == "no monthly cap on voice.stt, voice.tts: audio: session 1 USD"
    # A budget covering only one purpose leaves the other uncovered.
    AIBudget.objects.filter(name="audio").update(purposes=["voice.stt"], monthly_limit_usd=5)
    result = _voice_config()["voice_budget"]
    assert (result.status, result.detail) == (
        FAIL,
        "no active budget covers voice.tts: run seed_ai_defaults",
    )
    AIBudget.objects.filter(name="audio").update(
        purposes=["voice.stt", "voice.tts"], is_active=False
    )
    assert _voice_config()["voice_budget"].status == FAIL


@pytest.fixture
def browser_run(monkeypatch):
    runs = []
    outcome = {"code": 0, "raise": None}

    def fake_run(argv, *, timeout):
        runs.append((argv, timeout))
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return outcome["code"]

    monkeypatch.setattr(preflight, "run_command", fake_run)
    return runs, outcome


def test_voice_mute_warns_when_not_configured(settings, browser_run):
    runs, _ = browser_run
    settings.PREFLIGHT_VOICE_MUTE_CMD = ""
    assert preflight.check_voice_mute() == [
        CheckResult("voice_mute", WARN, "PREFLIGHT_VOICE_MUTE_CMD not set")
    ]
    assert runs == []


def test_voice_mute_runs_the_browser_spec_without_a_shell(settings, browser_run):
    runs, outcome = browser_run
    settings.PREFLIGHT_VOICE_MUTE_CMD = (
        "npm --prefix ../frontend run test:e2e -- 'e2e/voice-mute.e2e.ts'"
    )
    settings.PREFLIGHT_VOICE_MUTE_TIMEOUT_SECONDS = 99
    assert preflight.check_voice_mute()[0].status == PASS
    assert runs == [
        (
            ["npm", "--prefix", "../frontend", "run", "test:e2e", "--", "e2e/voice-mute.e2e.ts"],
            99,
        )
    ]
    outcome["code"] = 1
    assert preflight.check_voice_mute() == [
        CheckResult("voice_mute", FAIL, "browser run exited 1: muted tab not silent")
    ]


def test_voice_mute_timeout_and_missing_binary_fail(settings, browser_run):
    import subprocess

    _, outcome = browser_run
    settings.PREFLIGHT_VOICE_MUTE_CMD = "npx playwright test"
    settings.PREFLIGHT_VOICE_MUTE_TIMEOUT_SECONDS = 5
    outcome["raise"] = subprocess.TimeoutExpired(["npx"], 5)
    assert preflight.check_voice_mute()[0].detail == "browser run timed out after 5s"
    outcome["raise"] = FileNotFoundError("npx: /secret/path")
    result = preflight.check_voice_mute()[0]
    assert (result.status, result.detail) == (FAIL, "browser run failed: FileNotFoundError")


def test_voice_mute_unparsable_command_fails(settings, browser_run):
    runs, _ = browser_run
    settings.PREFLIGHT_VOICE_MUTE_CMD = "npx 'unterminated"
    assert preflight.check_voice_mute()[0].status == FAIL
    assert runs == []


def test_run_command_drops_the_output(capfd):
    import sys

    code = preflight.run_command(
        [sys.executable, "-c", "print('cookie=SECRET'); raise SystemExit(3)"], timeout=30
    )
    assert code == 3
    assert "SECRET" not in capfd.readouterr().out


# --- runner and command ------------------------------------------------------


def test_a_crashing_check_becomes_one_fail_row():
    def crash():
        raise KeyError("boom")

    results = preflight.run_preflight(
        checks=(("crashy", crash), ("voice_mute", preflight.check_voice_mute))
    )
    assert [(r.name, r.status) for r in results] == [("crashy", FAIL), ("voice_mute", WARN)]


def test_the_voice_checks_are_registered():
    names = [name for name, _ in preflight.CHECKS]
    assert names.index("voice_mute") == names.index("voice_config") + 1


def test_summarize():
    results = [CheckResult("a", PASS), CheckResult("b", WARN), CheckResult("c", FAIL)]
    assert preflight.summarize(results) == "1 pass / 1 fail / 1 warn"


def _stub_infra(monkeypatch, *, celery_replies):
    from nodes.keys import signing_key_id

    monkeypatch.setattr(preflight, "redis_ping", lambda url, *, timeout: True)
    monkeypatch.setattr(preflight, "celery_ping", lambda *, timeout: celery_replies)
    # Phase 9 closeout: the node checks' worker and conformance seams (the
    # node checks themselves: ops/tests/test_preflight_nodes.py).
    monkeypatch.setattr(preflight, "worker_signing_key_id", lambda *, timeout: signing_key_id())
    monkeypatch.setattr(preflight, "node_conformance_drift", lambda: [])
    # Phase 10 closeout: the escape suite's seam (the control checks
    # themselves: ops/tests/test_preflight_control.py).
    monkeypatch.setattr(preflight, "escape_suite_report", lambda: (True, 32, 32))
    # Phase 8B closeout: `seed_ai_defaults` adds the fake embedder after the
    # test database's `post_migrate` index build, so build its HNSW indexes
    # the way a deploy does (`retrieval_indexes`).
    call_command("build_vector_indexes", stdout=StringIO())


def test_command_all_green_exits_zero(settings, monkeypatch):
    settings.AI_FAKE_PROVIDER = True
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.REDIS_URL = "redis://redis:6379/0"
    call_command("seed_ai_defaults", stdout=StringIO())
    _stub_infra(monkeypatch, celery_replies=[{"w": {"ok": "pong"}}])
    out = StringIO()
    call_command("preflight", stdout=out)
    lines = out.getvalue().strip().splitlines()
    assert "PASS  postgres  SELECT 1" in lines
    assert "WARN  voice_mute  PREFLIGHT_VOICE_MUTE_CMD not set" in lines
    assert "PASS  voice_budget  audio: monthly 20 USD, session 1 USD" in lines
    assert "PASS  provider:fake-stt-local  model fake-stt-1 transcribed the fixture WAV" in lines
    assert re.fullmatch(r"\d+ pass / 0 fail / \d+ warn", lines[-1])


def test_command_failure_exits_non_zero(settings, monkeypatch):
    settings.AI_FAKE_PROVIDER = True
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.REDIS_URL = "redis://redis:6379/0"
    call_command("seed_ai_defaults", stdout=StringIO())
    _stub_infra(monkeypatch, celery_replies=[])  # no worker
    out = StringIO()
    with pytest.raises(SystemExit) as exc:
        call_command("preflight", stdout=out)
    assert exc.value.code == 1
    text = out.getvalue()
    assert "FAIL  celery  no worker answered the ping" in text
    assert "1 fail" in text.strip().splitlines()[-1]


def test_command_no_model_calls(settings, monkeypatch):
    settings.AI_FAKE_PROVIDER = True
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.REDIS_URL = "redis://redis:6379/0"
    call_command("seed_ai_defaults", stdout=StringIO())
    _stub_infra(monkeypatch, celery_replies=[])
    out = StringIO()
    call_command("preflight", "--no-model-calls", stdout=out)
    assert "WARN  provider:fake-local  skipped (--no-model-calls)" in out.getvalue()
    assert not ModelUsage.objects.exists()


# --- Phase 6 branch 5: the last result for the HUD's health panel ---


def test_command_records_the_last_result_for_the_hud(settings, monkeypatch):
    from django.core.cache import cache

    settings.AI_FAKE_PROVIDER = True
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.REDIS_URL = "redis://redis:6379/0"
    call_command("seed_ai_defaults", stdout=StringIO())
    _stub_infra(monkeypatch, celery_replies=[])  # no worker -> one failure
    with pytest.raises(SystemExit):
        call_command("preflight", stdout=StringIO())
    summary = cache.get(settings.HUD_PREFLIGHT_CACHE_KEY)
    assert summary["status"] == FAIL
    assert summary["fail"] == 1
    assert summary["failed_checks"] == ["celery"]
    assert summary["at"]
    # Names and counts only: no detail strings (hosts, env var names).
    assert "no worker" not in str(summary)


def test_record_last_result_survives_a_cache_outage(monkeypatch):
    from ops.management.commands import preflight as command

    def boom(*args, **kwargs):
        raise ConnectionError("cache down")

    monkeypatch.setattr(command.cache, "set", boom)
    summary = command.record_last_result([CheckResult("a", PASS), CheckResult("b", WARN)])
    assert summary["status"] == WARN
    assert summary["pass"] == 1 and summary["warn"] == 1
