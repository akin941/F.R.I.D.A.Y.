"""`HttpFridayClient`: the child's only network surface (Phase 11 brief
D4, D8).

The child calls exactly five Friday endpoints and nothing else -- no
provider, no other host:

    POST /api/v1/voice/live/sessions             open (or reuse) the live conversation
    POST /api/v1/voice/transcriptions            the utterance WAV -> text (gateway STT)
    POST /api/v1/ai/conversations/{id}/messages  the voice turn (SSE reply)
    POST /api/v1/voice/speech                    one sentence -> WAV (gateway TTS)
    GET  /api/v1/auth/nodes/voice-policy         the always-on verdict (branch 5)

Every request carries the short-lived `node_voice` token the host handed
over in a `credential` frame (memory only, never logged, never on argv).
Each call records the credential *generation* it was sent with, so the
pipeline can tell a 401 caused by a routine rotation (a newer credential
arrived meanwhile: retry once) from a refusal (D8 "Errors").

TLS (D6 `credential.ca_sha256`): an `https://` server is verified against
the default (certifi) bundle, or -- when the install recorded one
(`friday_voice.tls`, `friday-voice ca set`) or a harness passes
`--ca-file` -- against that CA file **instead**. Certificate and hostname
verification are never turned off. The TLS handshake is then checked,
**before any request byte is sent**, for a certificate in the verified
chain whose DER sha256 equals the pinned fingerprint (an httpcore trace
hook on `connection.start_tls.complete`); a TLS connection without a pin
is refused too. A loopback `http://` dev server (the only plain-HTTP form
the schema accepts) has no pin. A CA file that cannot be loaded fails
closed (`tls_ca_file_unreadable`), never falling back to the default.

The client is synchronous: the pipeline runs every call on a worker
(`friday_voice.pipeline.ThreadWorker`), never on the protocol loop.
"""

import hashlib
import json
import ssl
from dataclasses import dataclass, field

import httpx

from . import config, tls

SESSIONS_PATH = "/api/v1/voice/live/sessions"
TRANSCRIPTIONS_PATH = "/api/v1/voice/transcriptions"
SPEECH_PATH = "/api/v1/voice/speech"
POLICY_PATH = "/api/v1/auth/nodes/voice-policy"
CHAT_PATH = "/api/v1/ai/conversations/{}/messages"

TRACE_HEADER = "X-Friday-Trace"
TRIGGER_HEADER = "X-Friday-Voice-Trigger"
TRIGGERS = ("wake", "ptt", "barge_in")

# Error codes the pipeline acts on (D8, D11, D12).
CODE_LIVE_MUTED = "voice.live_muted"
CODE_ALWAYS_ON_NOT_PERMITTED = "voice.always_on_not_permitted"
CODE_NETWORK = "network"
CODE_PIN = "tls_pin_mismatch"
CODE_CA_FILE = "tls_ca_file_unreadable"
CODE_BAD_RESPONSE = "bad_response"


class FridayError(Exception):
    """A Friday request failed. `status` is the HTTP status (0 for a
    network or TLS failure), `code` a short machine code (never the
    response text), `generation` the credential generation it was sent
    with. `latency_ms` is a scripted latency (FakeFriday) applied before
    the failure is seen."""

    latency_ms = 0

    def __init__(self, status, code="", *, generation=0, network=False):
        super().__init__(f"{status} {code}".strip())
        self.status = int(status)
        self.code = str(code or "")
        self.generation = int(generation)
        self.network = bool(network)


@dataclass
class Reply:
    data: object
    generation: int = 0
    latency_ms: int = 0  # scripted latency (FakeFriday); 0: the call already took its time


@dataclass
class Credential:
    token: str
    server_url: str
    ca_sha256: str | None
    generation: int


@dataclass
class ChatStream:
    """The voice turn's reply: iterate `(latency_ms, event)` pairs (event is
    the SSE JSON: `chunk`, `draft`, `done`, `error`); `close()` aborts."""

    events: object
    generation: int = 0
    _close: object = None
    latency_ms: int = 0  # the events carry their own latencies
    closed: bool = field(default=False)

    def __iter__(self):
        return iter(self.events)

    def close(self):
        self.closed = True
        if self._close is not None:
            self._close()


def _error_code(response) -> str:
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(body, dict):
        return ""
    code = body.get("code") or body.get("error_type") or ""
    detail = body.get("detail")
    if not code and isinstance(detail, dict):
        code = detail.get("code", "")
    return str(code)[:64] if isinstance(code, str) else ""


def chain_fingerprints(ssl_object) -> set:
    """DER sha256 of every certificate in the *verified* chain of a TLS
    connection (CPython 3.12 keeps `get_verified_chain` private; 3.13
    exposes it)."""
    getter = getattr(ssl_object, "get_verified_chain", None)
    if getter is None:
        inner = getattr(ssl_object, "_sslobj", None)
        getter = getattr(inner, "get_verified_chain", None)
    if getter is None:
        return set()
    out = set()
    for cert in getter() or ():
        der = cert if isinstance(cert, bytes) else cert.public_bytes(ssl._ssl.ENCODING_DER)
        out.add(hashlib.sha256(der).hexdigest())
    return out


class PinMismatch(Exception):
    pass


def pin_hook(ca_sha256):
    """An httpcore trace callback refusing the connection, right after the
    TLS handshake and before the request is written, unless the pinned
    CA is in the verified chain. A TLS connection without a pin (null is
    for a loopback `http://` server only) is refused: fail closed."""

    def trace(event_name, info):
        if event_name != "connection.start_tls.complete":
            return
        if ca_sha256 is None:
            raise PinMismatch(CODE_PIN)
        stream = info.get("return_value")
        ssl_object = stream.get_extra_info("ssl_object") if stream is not None else None
        if ssl_object is None or ca_sha256 not in chain_fingerprints(ssl_object):
            raise PinMismatch(CODE_PIN)

    return trace


def parse_sse(lines):
    """`data: <json>` lines -> event dicts (blank lines separate events)."""
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            yield event


class HttpFridayClient:
    def __init__(self, *, ca_file=None, ca_unusable=False, transport=None, timeout=None):
        """`ca_file`: the CA bundle trusted instead of the default one.
        `ca_unusable`: the install's TLS config could not be read
        (`friday_voice.tls.TlsConfigError`); every request is refused."""
        self._credential = None
        self._generation = 0
        self._ca_file = ca_file
        self._ca_unusable = bool(ca_unusable)
        self._transport = transport
        self._timeout = timeout or httpx.Timeout(
            config.HTTP_READ_TIMEOUT_S, connect=config.HTTP_CONNECT_TIMEOUT_S
        )
        self._http = None

    # --- credential ---

    @property
    def generation(self) -> int:
        return self._generation

    def set_credential(self, token, server_url, ca_sha256) -> None:
        self._generation += 1
        previous = self._credential
        self._credential = Credential(token, server_url.rstrip("/"), ca_sha256, self._generation)
        if previous is None or (previous.server_url, previous.ca_sha256) != (
            self._credential.server_url,
            ca_sha256,
        ):
            self._reset_http()

    def _reset_http(self):
        old, self._http = self._http, None
        if old is not None:
            old.close()

    def _client(self):
        if self._http is None:
            if self._transport is not None:
                self._http = httpx.Client(transport=self._transport, timeout=self._timeout)
            else:
                if self._ca_unusable:
                    raise FridayError(0, CODE_CA_FILE, generation=self._generation)
                try:
                    context = tls.load_context(self._ca_file)
                except tls.TlsConfigError as exc:
                    raise FridayError(0, CODE_CA_FILE, generation=self._generation) from exc
                self._http = httpx.Client(verify=context, timeout=self._timeout, trust_env=False)
        return self._http

    def _current(self) -> Credential:
        credential = self._credential
        if credential is None:
            raise FridayError(401, "credential_missing")
        return credential

    def _headers(self, credential, trace_id="", trigger=""):
        headers = {"Authorization": f"Bearer {credential.token}"}
        if trace_id:
            headers[TRACE_HEADER] = trace_id
        if trigger:
            headers[TRIGGER_HEADER] = trigger
        return headers

    def _request(self, method, path, credential, **kwargs):
        extensions = {"trace": pin_hook(credential.ca_sha256)}
        try:
            response = self._client().request(
                method, credential.server_url + path, extensions=extensions, **kwargs
            )
        except PinMismatch as exc:
            raise FridayError(0, CODE_PIN, generation=credential.generation) from exc
        except httpx.HTTPError as exc:
            if isinstance(exc.__context__, PinMismatch) or isinstance(exc.__cause__, PinMismatch):
                raise FridayError(0, CODE_PIN, generation=credential.generation) from exc
            raise FridayError(
                0, CODE_NETWORK, generation=credential.generation, network=True
            ) from exc
        if response.status_code >= 400:
            raise FridayError(
                response.status_code, _error_code(response), generation=credential.generation
            )
        return response

    # --- the five calls ---

    def open_session(self, trigger) -> Reply:
        credential = self._current()
        response = self._request(
            "POST", SESSIONS_PATH, credential, headers=self._headers(credential, trigger=trigger)
        )
        body = response.json()
        if not isinstance(body, dict) or "conversation_id" not in body:
            raise FridayError(response.status_code, CODE_BAD_RESPONSE)
        return Reply(
            {"conversation_id": body["conversation_id"], "session_id": body.get("session_id")},
            credential.generation,
        )

    def transcribe(self, conversation_id, wav, trace_id, trigger) -> Reply:
        credential = self._current()
        response = self._request(
            "POST",
            TRANSCRIPTIONS_PATH,
            credential,
            headers=self._headers(credential, trace_id, trigger),
            data={"conversation": str(conversation_id)},
            files={"audio": ("utterance.wav", wav, "audio/wav")},
        )
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise FridayError(response.status_code, CODE_BAD_RESPONSE)
        return Reply(
            {"text": body["text"], "language": str(body.get("language") or "")},
            credential.generation,
        )

    def chat(self, conversation_id, text, trace_id) -> ChatStream:
        credential = self._current()
        request = self._client().build_request(
            "POST",
            credential.server_url + CHAT_PATH.format(conversation_id),
            headers={**self._headers(credential, trace_id), "Accept": "text/event-stream"},
            json={"content": text, "input_mode": "voice"},
            extensions={"trace": pin_hook(credential.ca_sha256)},
        )
        try:
            response = self._client().send(request, stream=True)
        except PinMismatch as exc:
            raise FridayError(0, CODE_PIN, generation=credential.generation) from exc
        except httpx.HTTPError as exc:
            raise FridayError(
                0, CODE_NETWORK, generation=credential.generation, network=True
            ) from exc
        if response.status_code >= 400:
            try:
                response.read()
                code = _error_code(response)
            finally:
                response.close()
            raise FridayError(response.status_code, code, generation=credential.generation)

        def events():
            try:
                for event in parse_sse(response.iter_lines()):
                    yield 0, event
            except httpx.HTTPError as exc:
                raise FridayError(
                    0, CODE_NETWORK, generation=credential.generation, network=True
                ) from exc
            finally:
                response.close()

        return ChatStream(events(), credential.generation, response.close)

    def synthesize(self, conversation_id, text, language, trace_id, timings=None) -> Reply:
        credential = self._current()
        body = {"conversation": str(conversation_id), "text": text}
        if language:
            body["language"] = language
        if timings:
            body["timings"] = dict(timings)
        response = self._request(
            "POST", SPEECH_PATH, credential, headers=self._headers(credential, trace_id), json=body
        )
        return Reply(response.content, credential.generation)

    def voice_policy(self, model_sha256, threshold, refractory_ms) -> Reply:
        """The always-on verdict for the loaded model at the threshold and
        refractory period it runs at (D11; the arming policy is branch 5).
        `threshold` is in thousandths (config.WAKE_THRESHOLD)."""
        credential = self._current()
        params = {
            "model_sha256": model_sha256,
            "threshold": f"{int(threshold) / 1000:.4f}",
            "refractory_ms": str(int(refractory_ms)),
        }
        response = self._request(
            "GET", POLICY_PATH, credential, headers=self._headers(credential), params=params
        )
        return Reply(response.json(), credential.generation)

    def close(self):
        self._reset_http()
