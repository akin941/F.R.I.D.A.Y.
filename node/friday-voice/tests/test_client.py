"""`HttpFridayClient` (brief D4, D6, D8) over httpx's MockTransport: the
five endpoints and nothing else, the headers, the multipart upload, the
SSE reply, error codes, credential generations and the CA pin hook. No
socket is opened."""

import hashlib
import json

import httpx
import pytest

from friday_voice import client as client_module
from friday_voice.audio import encode_wav
from friday_voice.client import (
    FridayError,
    HttpFridayClient,
    PinMismatch,
    chain_fingerprints,
    parse_sse,
    pin_hook,
)

TOKEN = "jpat_" + "clientT0kenAbcdef" * 2
SERVER = "http://127.0.0.1:8001"
CONVERSATION = "11111111-1111-4111-8111-111111111111"
TRACE = "22222222-2222-4222-8222-222222222222"


class Recorder:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.responder(request)


def make(responder):
    recorder = Recorder(responder)
    friday = HttpFridayClient(transport=httpx.MockTransport(recorder))
    friday.set_credential(TOKEN, SERVER, None)
    return friday, recorder


def sse(*events):
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})


def test_open_session_posts_with_the_trigger():
    friday, rec = make(
        lambda r: httpx.Response(201, json={"conversation_id": CONVERSATION, "session_id": "s"})
    )
    reply = friday.open_session("ptt")
    assert reply.data == {"conversation_id": CONVERSATION, "session_id": "s"}
    (request,) = rec.requests
    assert (request.method, request.url.path) == ("POST", "/api/v1/voice/live/sessions")
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-Friday-Voice-Trigger"] == "ptt"


def test_transcribe_uploads_an_in_memory_wav_with_trace_and_trigger():
    friday, rec = make(lambda r: httpx.Response(200, json={"text": "merhaba", "language": "tr"}))
    wav = encode_wav(b"\x01\x00" * 1600)
    reply = friday.transcribe(CONVERSATION, wav, TRACE, "wake")
    assert reply.data == {"text": "merhaba", "language": "tr"}
    (request,) = rec.requests
    assert request.url.path == "/api/v1/voice/transcriptions"
    assert request.headers["X-Friday-Trace"] == TRACE
    assert request.headers["X-Friday-Voice-Trigger"] == "wake"
    body = request.content
    assert wav in body and CONVERSATION.encode() in body
    assert request.headers["Content-Type"].startswith("multipart/form-data")


def test_chat_posts_a_voice_turn_and_streams_the_events():
    events = [
        {"type": "chunk", "text": "Merhaba. "},
        {"type": "draft", "kind": "task"},
        {"type": "done", "message_id": "m"},
    ]
    friday, rec = make(lambda r: sse(*events))
    stream = friday.chat(CONVERSATION, "bugün ne var", TRACE)
    assert [event for _latency, event in stream] == events
    (request,) = rec.requests
    assert request.url.path == f"/api/v1/ai/conversations/{CONVERSATION}/messages"
    assert json.loads(request.content) == {"content": "bugün ne var", "input_mode": "voice"}
    assert request.headers["X-Friday-Trace"] == TRACE


def test_chat_refused_before_the_stream_raises_with_the_code():
    friday, _ = make(
        lambda r: httpx.Response(423, json={"detail": "x", "code": "voice.live_muted"})
    )
    with pytest.raises(FridayError) as caught:
        friday.chat(CONVERSATION, "x", TRACE)
    assert (caught.value.status, caught.value.code) == (423, "voice.live_muted")


def test_synthesize_sends_language_and_timings_and_returns_the_wav():
    wav = encode_wav(b"\x00\x00" * 160)
    friday, rec = make(lambda r: httpx.Response(200, content=wav))
    reply = friday.synthesize(CONVERSATION, "Tamam.", "tr", TRACE, {"total_ms": 900})
    assert reply.data == wav
    body = json.loads(rec.requests[0].content)
    assert body == {
        "conversation": CONVERSATION,
        "text": "Tamam.",
        "language": "tr",
        "timings": {"total_ms": 900},
    }


def test_voice_policy_sends_the_threshold_as_a_decimal():
    friday, rec = make(lambda r: httpx.Response(200, json={"always_on_permitted": False}))
    friday.voice_policy("a" * 64, 500, 2000)
    params = dict(rec.requests[0].url.params)
    assert params == {"model_sha256": "a" * 64, "threshold": "0.5000", "refractory_ms": "2000"}
    assert rec.requests[0].url.path == "/api/v1/auth/nodes/voice-policy"


def test_every_path_is_one_of_the_five_endpoints():
    allowed = {
        client_module.SESSIONS_PATH,
        client_module.TRANSCRIPTIONS_PATH,
        client_module.SPEECH_PATH,
        client_module.POLICY_PATH,
        client_module.CHAT_PATH,
    }
    paths = {value for name, value in vars(client_module).items() if name.endswith("_PATH")}
    assert paths == allowed


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {"detail": "x"}, ""),
        (
            403,
            {"detail": "x", "code": "voice.always_on_not_permitted"},
            "voice.always_on_not_permitted",
        ),
        (429, {"detail": {"code": "throttled"}}, "throttled"),
        (500, "not json", ""),
    ],
)
def test_errors_carry_status_code_and_generation(status, body, code):
    def respond(_request):
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    friday, _ = make(respond)
    with pytest.raises(FridayError) as caught:
        friday.transcribe(CONVERSATION, b"RIFF", TRACE, "ptt")
    error = caught.value
    assert (error.status, error.code, error.generation, error.network) == (status, code, 1, False)
    assert str(error) == f"{status} {code}".strip()  # the response text is never echoed


def test_a_network_failure_is_status_zero_and_flagged():
    def respond(request):
        raise httpx.ConnectError("refused", request=request)

    friday, _ = make(respond)
    with pytest.raises(FridayError) as caught:
        friday.open_session("ptt")
    assert caught.value.status == 0 and caught.value.network


def test_generations_count_credentials_and_tag_each_request():
    friday, _ = make(lambda r: httpx.Response(401, json={}))
    with pytest.raises(FridayError) as first:
        friday.open_session("ptt")
    friday.set_credential(TOKEN + "x", SERVER, None)
    assert friday.generation == 2
    assert first.value.generation == 1


def test_no_credential_no_request():
    friday = HttpFridayClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(FridayError) as caught:
        friday.open_session("ptt")
    assert caught.value.status == 401


def test_sse_parsing_skips_noise():
    lines = [
        "",
        ": comment",
        "data: {not json}",
        'data: {"type": "chunk", "text": "a"}',
        "data: []",
    ]
    assert list(parse_sse(lines)) == [{"type": "chunk", "text": "a"}]


# --- the CA pin (checked before any request byte) ---


class FakeCert:
    def __init__(self, der):
        self.der = der

    def public_bytes(self, _encoding):
        return self.der


class FakeSSLObject:
    def __init__(self, chain):
        self._chain = chain

    def get_verified_chain(self):
        return [FakeCert(der) for der in self._chain]


class FakeStream:
    def __init__(self, ssl_object):
        self.ssl_object = ssl_object

    def get_extra_info(self, name):
        return self.ssl_object if name == "ssl_object" else None


CA = b"ca certificate der"
LEAF = b"leaf certificate der"
CA_SHA = hashlib.sha256(CA).hexdigest()


def test_the_pin_passes_when_the_ca_is_in_the_verified_chain():
    hook = pin_hook(CA_SHA)
    hook("connection.start_tls.complete", {"return_value": FakeStream(FakeSSLObject([LEAF, CA]))})
    assert chain_fingerprints(FakeSSLObject([LEAF, CA])) == {
        hashlib.sha256(LEAF).hexdigest(),
        CA_SHA,
    }


@pytest.mark.parametrize("chain", [[LEAF], [], None])
def test_the_pin_refuses_any_other_chain(chain):
    hook = pin_hook(CA_SHA)
    stream = FakeStream(FakeSSLObject(chain) if chain is not None else None)
    with pytest.raises(PinMismatch):
        hook("connection.start_tls.complete", {"return_value": stream})


def test_the_pin_only_looks_at_the_tls_handshake():
    pin_hook(CA_SHA)("http11.send_request_headers.started", {})
    pin_hook(None)("http11.send_request_headers.started", {})  # loopback http: no pin


def test_a_tls_handshake_without_a_pin_is_refused():
    stream = FakeStream(FakeSSLObject([LEAF, CA]))
    with pytest.raises(PinMismatch):
        pin_hook(None)("connection.start_tls.complete", {"return_value": stream})


def test_a_pin_failure_is_reported_as_such():
    def respond(request):
        request.extensions["trace"](
            "connection.start_tls.complete",
            {"return_value": FakeStream(FakeSSLObject([LEAF]))},
        )
        return httpx.Response(200, json={})

    recorder = Recorder(respond)
    friday = HttpFridayClient(transport=httpx.MockTransport(recorder))
    friday.set_credential(TOKEN, "https://friday.example", CA_SHA)
    with pytest.raises(FridayError) as caught:
        friday.open_session("ptt")
    assert caught.value.code == "tls_pin_mismatch" and caught.value.status == 0
