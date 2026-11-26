"""The node credential (Phase 13 brief D10, Q4: a 0600 file now, Secret
Service later).

`$XDG_STATE_HOME/friday-node/credential` holds, as JSON, what pairing
returned once plus what the node pinned:

    {"v": 1, "node_id": ..., "node_secret": "jnd_...",
     "server_url": "https://friday-home.local", "gateway_path": "/ws/node/v1",
     "signing_keys": [{"key_id": ..., "public_key_b64": ...}],
     "tls": {"ca_pem": ... | null, "spki_sha256": ... | null},
     "paired_at": <unix seconds>, "secret_issued_at": <unix seconds>,
     "rotate_after_days": 30}

The daemon refuses to start when the file is group/other readable, owned by
another uid, or a symlink (`paths.read_private`). The secret never reaches a
log, an exception message or `repr()`: only the node id and the server host
are ever printed.

`with_server_url` is the VPS re-point (ADR-017 decision 7): the server URL
changes, every pin stays. `with_secret` is a rotation (`rotation.py`,
`POST /api/v1/auth/nodes/credential/rotate`, Phase 9 D1): the secret and its
issue time change, nothing else.
"""

import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from friday_node_linux import paths
from friday_node_linux._vendor.nodes.protocol.signing import (
    SignatureError,
    key_id_for,
    load_public_key_b64,
)
from friday_node_linux.tls import Pins

CREDENTIAL_VERSION = 1
DEFAULT_ROTATE_AFTER_DAYS = 30
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_KEY_ID = re.compile(r"^[0-9a-f]{16}$")
_SECRET = re.compile(r"^jnd_[A-Za-z0-9_-]{20,200}$")
_KEYS = frozenset(
    {
        "v",
        "node_id",
        "node_secret",
        "server_url",
        "gateway_path",
        "signing_keys",
        "tls",
        "paired_at",
        "secret_issued_at",
        "rotate_after_days",
    }
)


class CredentialError(Exception):
    """No usable credential: missing, unsafe or malformed."""


def check_server_url(url: str) -> str:
    """An `https://host[:port]` URL without path, query or credentials."""
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("the server URL must be https://<host>[:<port>]")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("the server URL must not carry credentials, a query or a fragment")
    if parts.path not in ("", "/"):
        raise ValueError("the server URL must not have a path")
    _ = parts.port  # raises ValueError for a bad port
    return f"https://{parts.netloc}"


def check_secret(secret) -> str:
    if not isinstance(secret, str) or not _SECRET.match(secret):
        raise ValueError("node_secret")
    return secret


def _int(value, name, *, minimum=0):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(name)
    return value


@dataclass(frozen=True)
class Credential:
    node_id: str
    node_secret: str = field(repr=False)
    server_url: str
    gateway_path: str
    signing_keys: tuple  # ((key_id, public_key_b64), ...)
    pins: Pins
    paired_at: int
    secret_issued_at: int
    rotate_after_days: int = DEFAULT_ROTATE_AFTER_DAYS

    def __repr__(self):  # never print the secret
        return f"<Credential node={self.node_id} server={self.host}>"

    __str__ = __repr__

    @property
    def host(self) -> str:
        return urlsplit(self.server_url).hostname

    @property
    def port(self) -> int:
        return urlsplit(self.server_url).port or 443

    def pinned(self) -> dict:
        """`{key_id: public key bytes}` for the node's verifier."""
        return {key_id: load_public_key_b64(public) for key_id, public in self.signing_keys}

    def rotation_due(self, now) -> bool:
        return now - self.secret_issued_at >= self.rotate_after_days * 86400

    def document(self) -> dict:
        return {
            "v": CREDENTIAL_VERSION,
            "node_id": self.node_id,
            "node_secret": self.node_secret,
            "server_url": self.server_url,
            "gateway_path": self.gateway_path,
            "signing_keys": [
                {"key_id": key_id, "public_key_b64": public} for key_id, public in self.signing_keys
            ],
            "tls": self.pins.document(),
            "paired_at": self.paired_at,
            "secret_issued_at": self.secret_issued_at,
            "rotate_after_days": self.rotate_after_days,
        }

    @classmethod
    def from_document(cls, body) -> "Credential":
        if not isinstance(body, dict) or set(body) != _KEYS or body["v"] != CREDENTIAL_VERSION:
            raise CredentialError("the credential file is malformed")
        try:
            node_id = body["node_id"]
            if not isinstance(node_id, str) or not _UUID.match(node_id):
                raise ValueError("node_id")
            gateway_path = body["gateway_path"]
            if not isinstance(gateway_path, str) or not gateway_path.startswith("/"):
                raise ValueError("gateway_path")
            signing_keys = []
            for entry in body["signing_keys"]:
                key_id, public = entry["key_id"], entry["public_key_b64"]
                if not isinstance(key_id, str) or not _KEY_ID.match(key_id):
                    raise ValueError("signing key id")
                if key_id_for(load_public_key_b64(public)) != key_id:
                    raise ValueError("signing key id does not match the key")
                signing_keys.append((key_id, public))
            if not signing_keys:
                raise ValueError("signing keys")
            return cls(
                node_id=node_id,
                node_secret=check_secret(body["node_secret"]),
                server_url=check_server_url(body["server_url"]),
                gateway_path=gateway_path,
                signing_keys=tuple(signing_keys),
                pins=Pins.from_document(body["tls"]),
                paired_at=_int(body["paired_at"], "paired_at"),
                secret_issued_at=_int(body["secret_issued_at"], "secret_issued_at"),
                rotate_after_days=_int(body["rotate_after_days"], "rotate_after_days", minimum=1),
            )
        except (KeyError, TypeError, ValueError, SignatureError) as exc:
            # The message names the field only, never a value.
            raise CredentialError(f"the credential file is malformed ({exc})") from None

    @classmethod
    def from_pairing(cls, response, *, server_url, pins, now) -> "Credential":
        """The credential for a `POST /api/v1/nodes/pair` 201 body."""
        if not isinstance(response, dict):
            raise CredentialError("the pairing response is malformed")
        try:
            keys = [
                {"key_id": entry["key_id"], "public_key_b64": entry["public_key_b64"]}
                for entry in response["server_signing_keys"]
            ]
            body = {
                "v": CREDENTIAL_VERSION,
                "node_id": response["node_id"],
                "node_secret": response["node_secret"],
                "server_url": server_url,
                "gateway_path": response["gateway_path"],
                "signing_keys": keys,
                "tls": pins.document(),
                "paired_at": int(now),
                "secret_issued_at": int(now),
                "rotate_after_days": response.get("rotate_after_days", DEFAULT_ROTATE_AFTER_DAYS),
            }
        except (KeyError, TypeError) as exc:
            raise CredentialError("the pairing response is malformed") from exc
        return cls.from_document(body)

    def with_server_url(self, url) -> "Credential":
        return dataclasses.replace(self, server_url=check_server_url(url))

    def with_secret(self, secret, *, now, rotate_after_days=None) -> "Credential":
        try:
            secret = check_secret(secret)
            days = _int(
                self.rotate_after_days if rotate_after_days is None else rotate_after_days,
                "rotate_after_days",
                minimum=1,
            )
        except ValueError as exc:
            raise CredentialError(f"the rotation response is malformed ({exc})") from None
        return dataclasses.replace(
            self, node_secret=secret, secret_issued_at=int(now), rotate_after_days=days
        )


def load_credential(path) -> Credential:
    path = Path(path)
    try:
        data = paths.read_private(path)
    except FileNotFoundError:
        raise CredentialError("not paired: run `friday-node-linux pair` first") from None
    except paths.UnsafePath as exc:
        raise CredentialError(f"refusing the credential file: {exc}") from None
    try:
        body = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise CredentialError("the credential file is not valid JSON") from None
    return Credential.from_document(body)


def save_credential(path, credential: Credential) -> None:
    body = json.dumps(credential.document(), indent=2, sort_keys=True) + "\n"
    paths.write_private_atomic(path, body.encode("utf-8"))
