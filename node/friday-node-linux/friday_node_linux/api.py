"""The node's two HTTPS calls to the server, over pinned TLS (D10):

- `POST /api/v1/nodes/pair` (anonymous; the pairing code is the
  credential), once, by `friday-node-linux pair`;
- `POST /api/v1/auth/nodes/credential/rotate` (the current node secret as
  `Authorization: Bearer`; Phase 9 D1), by the daemon when the secret is
  `rotate_after_days` old, or by `friday-node-linux rotate`.

The TLS pins are checked right after the handshake, before the request
line, the pairing code or the secret is written. Nothing is logged here.
"""

import http.client
import json
import time
from urllib.parse import urlsplit

from friday_node_linux import __version__
from friday_node_linux.credential import Credential, CredentialError
from friday_node_linux.tls import PinError, Pins

PAIR_PATH = "/api/v1/nodes/pair"
ROTATE_PATH = "/api/v1/auth/nodes/credential/rotate"
USER_AGENT = f"friday-node-linux/{__version__}"
MAX_RESPONSE_BYTES = 1024 * 1024


class RotationError(Exception):
    """The server refused the rotation (`status`, `code`), or it failed."""

    def __init__(self, status, code=None):
        super().__init__(f"HTTP {status} {code or ''}".rstrip())
        self.status = status
        self.code = code


def post_json(server_url, path, body, *, pins: Pins, headers=None, timeout=15.0):
    """POST `body` over a pinned TLS connection. Returns (status, json)."""
    parts = urlsplit(server_url)
    host, port = parts.hostname, parts.port or 443
    sock = pins.connect(host, port, timeout=timeout)
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    connection.sock = sock
    try:
        connection.request(
            "POST",
            path,
            body=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "Host": parts.netloc,
                **(headers or {}),
            },
        )
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES)
    finally:
        connection.close()
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        data = {}
    return response.status, data if isinstance(data, dict) else {}


def rotate(credential: Credential, *, now=None, post=post_json) -> Credential:
    """The credential with a fresh secret from the server. Raises
    RotationError (refused / unreachable) or CredentialError (malformed)."""
    try:
        status, data = post(
            credential.server_url,
            ROTATE_PATH,
            {},
            pins=credential.pins,
            headers={"Authorization": f"Bearer {credential.node_secret}"},
        )
    except (OSError, PinError) as exc:
        raise RotationError(0, exc.__class__.__name__) from None
    if status != 200:
        raise RotationError(status, data.get("code"))
    if data.get("node_id") != credential.node_id:
        raise CredentialError("the rotation response names another node")
    return credential.with_secret(
        data.get("node_secret"),
        now=time.time() if now is None else now,
        rotate_after_days=data.get("rotate_after_days"),
    )
