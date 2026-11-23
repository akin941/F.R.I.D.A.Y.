"""The `compressor` sidecar (Phase 8B branch 6, brief D11, docs/mcp/headroom.md).

A small stdlib HTTP server with ONE endpoint, `POST /compress`, that calls
only `headroom.compress()`. No proxy mode, no `wrap`, no MCP, no model call,
no provider key. It runs on an `internal: true` compose network shared only
with web and worker, read-only, `cap_drop: ALL`, no published port.

Request:  `{"model": str, "messages": [{"role": "assistant", "content": str}, ...]}`
          (Friday's gateway stage sends only prior assistant messages).
Response: `{"messages", "tokens_before", "tokens_after", "transforms_applied",
          "lossless"}`; the same number of messages, in order.

Health (Phase 8B closeout, `manage.py preflight` `compressor`): `GET /healthz`
answers `{"status": "ok", "telemetry": "off"|"on", "offline": bool}`, read
from this process's environment (no Headroom call, no request body).

Refused: any other path (404), any method but POST on `/compress` and GET on
`/healthz` (405), a body over 1 MB (413, checked on Content-Length before
reading), malformed JSON or a wrong shape (400). The server never logs
message content.

Run: `python server.py` (env `COMPRESSOR_PORT`, default 8080). Tests inject a
fake compress function via `make_server(compress_fn=...)`, so they run
without Headroom installed.
"""

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Telemetry off and offline before Headroom is ever imported (D11).
TELEMETRY_OFF = (
    ("HEADROOM_TELEMETRY", "off"),
    ("HEADROOM_BEACON", "off"),
    ("DO_NOT_TRACK", "1"),
)
OFFLINE = (
    ("HEADROOM_OFFLINE", "1"),
    ("HF_HUB_OFFLINE", "1"),
)
for _key, _value in OFFLINE + TELEMETRY_OFF:
    os.environ.setdefault(_key, _value)

MAX_BODY_BYTES = 1024 * 1024
MAX_MESSAGES = 64
PATH = "/compress"
HEALTH_PATH = "/healthz"
ALLOWED_ROLES = {"assistant"}

logger = logging.getLogger("compressor")


class BadRequest(Exception):
    pass


def headroom_compress(messages, model):
    """The only Headroom call. `protect_recent=0`: the gateway already
    removed the final message and every user/system message, so nothing
    sent here is the active turn. `kompress_model="disabled"`: the prose ML
    compressor needs a HuggingFace model, which this image does not carry."""
    from headroom import compress

    result = compress(
        messages,
        model=model or "gpt-4o",
        protect_recent=0,
        kompress_model="disabled",
    )
    return {
        "messages": [
            {"role": m.get("role"), "content": m.get("content")} for m in result.messages
        ],
        "tokens_before": int(result.tokens_before or 0),
        "tokens_after": int(result.tokens_after or 0),
        "transforms_applied": [str(t) for t in (result.transforms_applied or [])],
        "lossless": result.lossless if isinstance(result.lossless, bool) else None,
    }


def parse_request(body):
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BadRequest("not JSON") from None
    if not isinstance(data, dict) or set(data) - {"model", "messages"}:
        raise BadRequest("unexpected keys")
    model = data.get("model", "")
    messages = data.get("messages")
    if not isinstance(model, str) or not isinstance(messages, list):
        raise BadRequest("bad shape")
    if not 0 < len(messages) <= MAX_MESSAGES:
        raise BadRequest("message count")
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in ALLOWED_ROLES
            or not isinstance(message["content"], str)
        ):
            raise BadRequest("bad message")
    return model[:128], messages


def checked_result(result, messages):
    """Keep the 1:1 mapping the gateway relies on: a result with another
    message count (Headroom may drop or merge) returns the input unchanged."""
    returned = result.get("messages") or []
    if len(returned) != len(messages) or any(
        not isinstance(m.get("content"), str) for m in returned
    ):
        before = int(result.get("tokens_before") or 0)
        return {
            "messages": messages,
            "tokens_before": before,
            "tokens_after": before,
            "transforms_applied": ["compressor:count_mismatch_reverted"],
            "lossless": True,
        }
    result["messages"] = [{"role": "assistant", "content": m["content"]} for m in returned]
    return result


def health():
    """The `/healthz` body: whether this process still runs with telemetry
    off and offline (an operator env override would flip it)."""
    telemetry_off = all(os.environ.get(key) == value for key, value in TELEMETRY_OFF)
    offline = all(os.environ.get(key) == value for key, value in OFFLINE)
    return {"status": "ok", "telemetry": "off" if telemetry_off else "on", "offline": offline}


def make_handler(compress_fn):
    class Handler(BaseHTTPRequestHandler):
        server_version = "friday-compressor"
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            # Method, path and status only; never a body.
            logger.info("%s %s", self.command, self.path.split("?", 1)[0])

        def _reply(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse_method(self):
            known = self.path in (PATH, HEALTH_PATH)
            self._reply(405 if known else 404, {"error": "refused"})

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _refuse_method

        def do_GET(self):
            if self.path != HEALTH_PATH:
                return self._refuse_method()
            return self._reply(200, health())

        def do_POST(self):
            if self.path == HEALTH_PATH:
                return self._reply(405, {"error": "refused"})
            if self.path != PATH:
                return self._reply(404, {"error": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reply(411, {"error": "length_required"})
            if length < 0 or length > MAX_BODY_BYTES:
                self.close_connection = True
                return self._reply(413, {"error": "too_large"})
            body = self.rfile.read(length)
            try:
                model, messages = parse_request(body)
            except BadRequest:
                return self._reply(400, {"error": "bad_request"})
            try:
                result = compress_fn(messages, model)
            except Exception:
                logger.exception("compress failed")
                return self._reply(500, {"error": "compress_failed"})
            return self._reply(200, checked_result(result, messages))

    return Handler


def make_server(host="0.0.0.0", port=8080, *, compress_fn=headroom_compress):
    return ThreadingHTTPServer((host, port), make_handler(compress_fn))


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    port = int(os.environ.get("COMPRESSOR_PORT", "8080"))
    server = make_server(port=port)
    logger.info("compressor listening on :%s%s", port, PATH)
    server.serve_forever()


if __name__ == "__main__":
    main()
