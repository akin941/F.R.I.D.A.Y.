"""The pre-send compression stage (Phase 8B branch 6, brief D11).

Default off (byte-identical payload, no sidecar request); gating (enabled,
bypass wins, unclassified never); every purpose classified; failure is open
(timeout, 5xx, malformed); exactly one `ModelUsage` with pre/post counts on
the sync and the stream path; `personal_data` calls allowed and flagged; the
real HTTP client against a localhost stub. No real sidecar, no real LLM."""

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from ai_core import compression
from ai_core.adapters.fake import FakeAdapter
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelUsage

pytestmark = pytest.mark.django_db

LONG_A1 = "Session list: " + "branch feat/x merged, tests green. " * 40
LONG_A2 = "Deploy log: " + "step ok in 3 s, no warnings. " * 40
MESSAGES = [
    {"role": "system", "content": "You are Friday.\n<core_blocks>self</core_blocks>"},
    {"role": "user", "content": "List my sessions."},
    {"role": "assistant", "content": LONG_A1},
    {"role": "user", "content": "And the deploy?"},
    {"role": "assistant", "content": LONG_A2},
    {"role": "user", "content": "Which one failed?"},
]


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_COMPRESSION_ENABLED = False
    settings.AI_COMPRESSION_PURPOSES = frozenset()
    return settings


@pytest.fixture
def provider(db):
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )


@pytest.fixture
def adapter_payloads(monkeypatch):
    """Every payload the (fake) provider adapter receives, sync and stream."""
    payloads = []
    original = FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        payloads.append(copy.deepcopy(messages))
        return original(self, purpose, messages)

    monkeypatch.setattr(FakeAdapter, "_text_for", text_for)
    return payloads


class FakeCompressor:
    """The HTTP stub's stand-in: halves each assistant message."""

    def __init__(self, behaviour="shorten"):
        self.behaviour = behaviour
        self.requests = []

    def compress(self, messages, *, model):
        self.requests.append({"model": model, "messages": copy.deepcopy(messages)})
        if self.behaviour == "timeout":
            raise compression.CompressorError("timeout")
        if self.behaviour == "http_503":
            raise compression.CompressorError("http_503")
        if self.behaviour == "garbage":
            return {"messages": [{"role": "user", "content": "x"}]}
        if self.behaviour == "explode":
            raise RuntimeError("bug")
        if self.behaviour == "identity":
            tokens = sum(len(m["content"]) // 4 for m in messages)
            return {
                "messages": messages,
                "tokens_before": tokens,
                "tokens_after": tokens,
                "transforms_applied": ["router:noop"],
                "lossless": True,
            }
        out = [
            {"role": "assistant", "content": m["content"][: len(m["content"]) // 2]}
            for m in messages
        ]
        return {
            "messages": out,
            "tokens_before": sum(len(m["content"]) // 4 for m in messages),
            "tokens_after": sum(len(m["content"]) // 4 for m in out),
            "transforms_applied": ["smart_crusher"],
            "lossless": False,
        }


@pytest.fixture
def sidecar(monkeypatch):
    fake = FakeCompressor()
    monkeypatch.setattr(compression, "get_client", lambda: fake)
    return fake


def _enable(settings, *purposes):
    settings.AI_COMPRESSION_ENABLED = True
    settings.AI_COMPRESSION_PURPOSES = frozenset(purposes)


def _complete(purpose="chat", messages=None, **kwargs):
    return InferenceGateway().complete(purpose, messages or copy.deepcopy(MESSAGES), **kwargs)


# --- default off ----------------------------------------------------------------------


def test_default_settings_are_off():
    assert django_settings.AI_COMPRESSION_TIMEOUT_MS == 300
    # Read as source: kernel apps may not import the project config.
    base = (Path(__file__).resolve().parents[2] / "config" / "settings" / "base.py").read_text()
    assert 'AI_COMPRESSION_ENABLED = env.bool("AI_COMPRESSION_ENABLED", default=False)' in base
    assert 'frozenset(env.list("AI_COMPRESSION_PURPOSES", default=[]))' in base


def test_off_by_default_the_payload_is_byte_identical_and_no_sidecar_request(
    provider, adapter_payloads, sidecar, settings
):
    sent = copy.deepcopy(MESSAGES)
    _complete(messages=sent)
    assert json.dumps(adapter_payloads[0]) == json.dumps(MESSAGES)
    assert sidecar.requests == []
    assert "compression" not in ModelUsage.objects.get().context_report


def test_listed_but_flag_off_and_flag_on_but_unlisted_do_nothing(
    provider, adapter_payloads, sidecar, settings
):
    settings.AI_COMPRESSION_PURPOSES = frozenset({"chat"})
    _complete()
    settings.AI_COMPRESSION_ENABLED = True
    settings.AI_COMPRESSION_PURPOSES = frozenset({"agent.read_step"})
    _complete()
    assert sidecar.requests == []
    assert all(p == MESSAGES for p in adapter_payloads)
    assert all("compression" not in r.context_report for r in ModelUsage.objects.all())


# --- gating -------------------------------------------------------------------------------


def test_an_enabled_purpose_is_compressed(provider, adapter_payloads, sidecar, settings):
    _enable(settings, "chat")
    original = copy.deepcopy(MESSAGES)
    caller_list = copy.deepcopy(MESSAGES)
    _complete(messages=caller_list)
    assert caller_list == original  # the caller's list is never mutated
    [request] = sidecar.requests
    assert request["model"] == "fake-1"
    assert request["messages"] == [
        {"role": "assistant", "content": LONG_A1},
        {"role": "assistant", "content": LONG_A2},
    ]
    payload = adapter_payloads[0]
    assert payload[2]["content"] == LONG_A1[: len(LONG_A1) // 2]
    assert payload[4]["content"] == LONG_A2[: len(LONG_A2) // 2]
    for index in (0, 1, 3, 5):  # system and every user message untouched
        assert payload[index] == MESSAGES[index]
    report = ModelUsage.objects.get().context_report["compression"]
    assert report["applied"] is True
    assert report["pre_tokens"] > report["post_tokens"] > 0
    assert report["transforms"] == ["smart_crusher"]
    assert report["lossless"] is False and report["error"] == ""
    assert report["sent_messages"] == 2


@pytest.mark.parametrize("purpose", sorted(compression.COMPRESSION_BYPASS))
def test_a_bypass_purpose_is_never_compressed_even_when_listed(
    purpose, provider, adapter_payloads, sidecar, settings
):
    _enable(settings, purpose)
    try:
        _complete(purpose=purpose)
    except Exception:  # noqa: BLE001 - a purpose's own fake reply may not parse; irrelevant
        pass
    assert sidecar.requests == []
    assert adapter_payloads[0] == MESSAGES
    report = ModelUsage.objects.get(purpose=purpose).context_report["compression"]
    assert report == {**report, "applied": False, "skipped": "bypass"}


def test_memory_eval_answer_is_bypassed_unconditionally():
    assert "memory.eval_answer" in compression.COMPRESSION_BYPASS
    assert compression.classify("memory.eval_answer") == "bypass"


def test_an_unclassified_purpose_is_never_compressed(provider, adapter_payloads, sidecar, settings):
    _enable(settings, "brand.new_purpose")
    _complete(purpose="brand.new_purpose")
    assert sidecar.requests == []
    report = ModelUsage.objects.get().context_report["compression"]
    assert report["skipped"] == "not_eligible" and report["applied"] is False


def test_only_assistant_messages_before_the_last_are_sent(provider, sidecar, settings):
    _enable(settings, "chat")
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "earlier reply " * 30},
        {"role": "tool", "content": "tool output " * 30},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a trailing assistant message " * 30},
    ]
    _complete(messages=messages)
    [request] = sidecar.requests
    assert [m["content"] for m in request["messages"]] == ["earlier reply " * 30]


def test_no_assistant_history_makes_no_sidecar_request(provider, sidecar, settings):
    _enable(settings, "chat")
    _complete(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}])
    assert sidecar.requests == []
    report = ModelUsage.objects.get().context_report["compression"]
    assert report["skipped"] == "no_assistant_history"


def test_a_longer_answer_never_inflates_a_message(
    provider, adapter_payloads, monkeypatch, settings
):
    class Inflating(FakeCompressor):
        def compress(self, messages, *, model):
            return {
                "messages": [{"role": "assistant", "content": m["content"] * 2} for m in messages],
                "tokens_before": 10,
                "tokens_after": 20,
            }

    monkeypatch.setattr(compression, "get_client", Inflating)
    _enable(settings, "chat")
    _complete()
    assert adapter_payloads[0] == MESSAGES
    report = ModelUsage.objects.get().context_report["compression"]
    assert report["applied"] is False and report["post_tokens"] == report["pre_tokens"]


# --- purpose classification -------------------------------------------------------------


def test_every_tiered_and_untiered_purpose_is_classified():
    purposes = set(django_settings.AI_PURPOSE_TIERS) | set(django_settings.AI_UNTIERED_PURPOSES)
    unclassified = sorted(p for p in purposes if compression.classify(p) is None)
    assert not unclassified, (
        "Classify each new purpose in ai_core/compression.py as COMPRESSION_BYPASS, "
        f"COMPRESSION_ELIGIBLE or COMPRESSION_NOT_APPLICABLE: {unclassified}"
    )


def test_the_classification_sets_are_disjoint_and_name_real_purposes():
    sets = (
        compression.COMPRESSION_BYPASS,
        compression.COMPRESSION_ELIGIBLE,
        compression.COMPRESSION_NOT_APPLICABLE,
    )
    for i, left in enumerate(sets):
        for right in sets[i + 1 :]:
            assert not left & right
    known = set(django_settings.AI_PURPOSE_TIERS) | set(django_settings.AI_UNTIERED_PURPOSES)
    assert set().union(*sets) <= known
    # D11's named members.
    assert {"ops.preflight", "router.intent", "agent.plan", "memory.reflection"} <= sets[0]
    assert "chat" in sets[1]
    assert {"memory.retrieval", "voice.stt", "harness.review"} <= sets[2]


def test_the_classification_test_fails_on_an_unclassified_purpose(settings):
    settings.AI_PURPOSE_TIERS = {**settings.AI_PURPOSE_TIERS, "new.purpose": "cheap"}
    with pytest.raises(AssertionError, match="new.purpose"):
        test_every_tiered_and_untiered_purpose_is_classified()


# --- failure is open -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("behaviour", "error"),
    [
        ("timeout", "timeout"),
        ("http_503", "http_503"),
        ("garbage", "invalid_response"),
        ("explode", "stage_error"),
    ],
)
def test_a_sidecar_failure_sends_the_original_messages(
    behaviour, error, provider, adapter_payloads, monkeypatch, settings
):
    monkeypatch.setattr(compression, "get_client", lambda: FakeCompressor(behaviour))
    _enable(settings, "chat")
    result = _complete()
    assert result.text
    assert adapter_payloads[0] == MESSAGES
    usage = ModelUsage.objects.get()
    assert usage.success is True
    report = usage.context_report["compression"]
    assert report["applied"] is False and report["error"] == error
    assert report["pre_tokens"] == report["post_tokens"] > 0


# --- accounting ----------------------------------------------------------------------------


def test_exactly_one_usage_row_with_pre_and_post_tokens(provider, sidecar, settings):
    _enable(settings, "chat")
    _complete()
    assert ModelUsage.objects.count() == 1
    report = ModelUsage.objects.get().context_report["compression"]
    assert {"applied", "pre_tokens", "post_tokens", "transforms", "lossless", "error"} <= set(
        report
    )


def test_the_stream_path_is_the_same(provider, adapter_payloads, sidecar, settings):
    _enable(settings, "chat")
    chunks = list(_complete(stream=True))
    assert chunks
    assert len(sidecar.requests) == 1
    assert adapter_payloads[0][2]["content"] == LONG_A1[: len(LONG_A1) // 2]
    assert adapter_payloads[0][5] == MESSAGES[5]
    assert ModelUsage.objects.count() == 1
    report = ModelUsage.objects.get().context_report["compression"]
    assert report["applied"] is True and report["pre_tokens"] > report["post_tokens"]


def test_the_stream_path_off_is_unchanged(provider, adapter_payloads, sidecar):
    list(_complete(stream=True))
    assert adapter_payloads[0] == MESSAGES and sidecar.requests == []
    assert "compression" not in ModelUsage.objects.get().context_report


def test_personal_data_calls_are_compressed_and_flagged(
    provider, adapter_payloads, sidecar, settings
):
    _enable(settings, "chat")
    _complete(personal_data=True)
    assert len(sidecar.requests) == 1
    usage = ModelUsage.objects.get()
    assert usage.context_report["personal_data"] is True
    assert usage.context_report["compression"]["personal_data"] is True
    assert usage.context_report["compression"]["applied"] is True


def test_the_caller_context_report_keys_survive(provider, sidecar, settings):
    _enable(settings, "chat")
    _complete(context_report={"eval_run_id": "r1", "eval_arm": "b"})
    report = ModelUsage.objects.get().context_report
    assert report["eval_run_id"] == "r1" and report["compression"]["applied"] is True


# --- the real HTTP client against a localhost stub --------------------------------------------


class _Stub(BaseHTTPRequestHandler):
    mode = "ok"
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.path, body))
        if self.mode == "slow":
            time.sleep(0.5)
        if self.mode == "500":
            self.send_response(500)
            self.end_headers()
            return
        data = json.dumps(
            {
                "messages": [
                    {"role": "assistant", "content": m["content"][:5]} for m in body["messages"]
                ],
                "tokens_before": 40,
                "tokens_after": 2,
                "transforms_applied": ["t"],
                "lossless": False,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def stub_server():
    _Stub.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    _Stub.mode = "ok"


def _client(httpd, timeout=2.0):
    return compression.CompressorClient(
        f"http://127.0.0.1:{httpd.server_address[1]}/compress", timeout
    )


def test_the_http_client_round_trip(stub_server):
    answer = _client(stub_server).compress(
        [{"role": "assistant", "content": "hello world"}], model="m"
    )
    assert answer["messages"] == [{"role": "assistant", "content": "hello"}]
    path, body = _Stub.seen[0]
    assert path == "/compress" and body == {
        "model": "m",
        "messages": [{"role": "assistant", "content": "hello world"}],
    }


def test_the_http_client_maps_timeout_and_5xx(stub_server):
    _Stub.mode = "slow"
    with pytest.raises(compression.CompressorError) as exc:
        _client(stub_server, timeout=0.1).compress(
            [{"role": "assistant", "content": "x"}], model="m"
        )
    assert exc.value.code == "timeout"
    _Stub.mode = "500"
    with pytest.raises(compression.CompressorError) as exc:
        _client(stub_server).compress([{"role": "assistant", "content": "x"}], model="m")
    assert exc.value.code == "http_500"


def test_the_http_client_refuses_a_body_over_one_megabyte(stub_server):
    big = [{"role": "assistant", "content": "x" * (compression.MAX_REQUEST_BYTES + 1)}]
    with pytest.raises(compression.CompressorError) as exc:
        _client(stub_server).compress(big, model="m")
    assert exc.value.code == "request_too_large" and _Stub.seen == []


def test_an_unreachable_sidecar_fails_open_through_the_gateway(
    provider, adapter_payloads, settings
):
    _enable(settings, "chat")
    settings.AI_COMPRESSION_URL = "http://127.0.0.1:9/compress"  # discard port: refused
    settings.AI_COMPRESSION_TIMEOUT_MS = 200
    _complete()
    assert adapter_payloads[0] == MESSAGES
    report = ModelUsage.objects.get().context_report["compression"]
    assert report["error"] in {"unreachable", "timeout"}


def test_the_gateway_uses_the_real_client_against_the_stub(
    provider, adapter_payloads, stub_server, settings
):
    _enable(settings, "chat")
    settings.AI_COMPRESSION_URL = f"http://127.0.0.1:{stub_server.server_address[1]}/compress"
    settings.AI_COMPRESSION_TIMEOUT_MS = 2000
    _complete()
    assert adapter_payloads[0][2]["content"] == LONG_A1[:5]
    report = ModelUsage.objects.get().context_report["compression"]
    assert (report["pre_tokens"], report["post_tokens"], report["applied"]) == (40, 2, True)
