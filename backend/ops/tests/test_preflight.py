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
from ai_core.adapters.base import AdapterError, AdapterResult, EmbedAdapterResult
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


def test_audio_provider_rows_warn_and_are_never_called(settings, monkeypatch, adapters):
    calls, _ = adapters
    settings.AI_FAKE_PROVIDER = False
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "k" * 20)
    _provider("stt-a", capability=ModelProvider.Capability.STT)
    _provider("tts-a", capability=ModelProvider.Capability.TTS)
    results = {r.name: r for r in preflight.check_providers()}
    assert {n: r.status for n, r in results.items()} == {
        "provider:stt-a": WARN,
        "provider:tts-a": WARN,
    }
    assert results["provider:tts-a"].detail == "audio provider: not checked yet"
    assert calls == []
    assert not ModelUsage.objects.exists()


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


def test_voice_mute_is_a_standing_warning():
    assert preflight.check_voice_mute() == [
        CheckResult("voice_mute", WARN, "voice layer not built yet (Phase 6)")
    ]


# --- runner and command ------------------------------------------------------


def test_a_crashing_check_becomes_one_fail_row():
    def crash():
        raise KeyError("boom")

    results = preflight.run_preflight(
        checks=(("crashy", crash), ("voice_mute", preflight.check_voice_mute))
    )
    assert [(r.name, r.status) for r in results] == [("crashy", FAIL), ("voice_mute", WARN)]


def test_summarize():
    results = [CheckResult("a", PASS), CheckResult("b", WARN), CheckResult("c", FAIL)]
    assert preflight.summarize(results) == "1 pass / 1 fail / 1 warn"


def _stub_infra(monkeypatch, *, celery_replies):
    monkeypatch.setattr(preflight, "redis_ping", lambda url, *, timeout: True)
    monkeypatch.setattr(preflight, "celery_ping", lambda *, timeout: celery_replies)


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
    assert "WARN  voice_mute  voice layer not built yet (Phase 6)" in lines
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
