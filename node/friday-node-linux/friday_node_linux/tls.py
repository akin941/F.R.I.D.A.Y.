"""TLS pinning for the Linux node (Phase 13 brief D10; ADR-017: node WSS
is TLS with a pinned server certificate or a private CA).

A node pins, at pairing, one or both of:

- `ca_pem`: the private CA (or the self-signed server certificate itself):
  the chain must verify against it **and** the host name must match;
- `spki_sha256`: the sha256 of the served certificate's
  SubjectPublicKeyInfo (hex; the server's `NODE_TLS_PIN_SHA256` accepts the
  same value as hex or base64).

There is no trust-on-first-use and no system trust store: a node with
neither pin refuses to connect. The pin is checked right after the
handshake, before a single application byte (the pairing code, the node
secret) is sent.
"""

import base64
import binascii
import hashlib
import re
import socket
import ssl
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import serialization

_HEX = re.compile(r"^[0-9a-f]{64}$")


class PinError(Exception):
    """The server's certificate does not match the node's pins."""


def parse_spki_pin(value: str) -> str:
    """A SPKI sha256 pin given as hex or base64, as lowercase hex."""
    value = (value or "").strip()
    if _HEX.match(value.lower()):
        return value.lower()
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("a SPKI pin is 64 hex characters or the base64 of 32 bytes") from exc
    if len(raw) != 32:
        raise ValueError("a SPKI pin is the sha256 of the SubjectPublicKeyInfo (32 bytes)")
    return raw.hex()


def spki_sha256(der_certificate: bytes) -> str:
    certificate = x509.load_der_x509_certificate(der_certificate)
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(spki).hexdigest()


def check_ca_pem(ca_pem: str) -> str:
    """`ca_pem` if it holds at least one PEM certificate, else ValueError."""
    try:
        certificates = x509.load_pem_x509_certificates(ca_pem.encode("ascii"))
    except ValueError as exc:
        raise ValueError("the CA file holds no PEM certificate") from exc
    if not certificates:
        raise ValueError("the CA file holds no PEM certificate")
    return ca_pem


@dataclass(frozen=True)
class Pins:
    ca_pem: str | None = None
    spki_sha256: str | None = None

    def __post_init__(self):
        if not self.ca_pem and not self.spki_sha256:
            raise ValueError("a node needs a TLS pin: --ca or --tls-pin-sha256")

    def context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if self.ca_pem:
            context.load_verify_locations(cadata=self.ca_pem)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
        else:
            # SPKI-only: the chain is not verified, the key is (below).
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def verify(self, tls_socket) -> None:
        if self.spki_sha256:
            der = tls_socket.getpeercert(binary_form=True)
            if not der or spki_sha256(der) != self.spki_sha256:
                raise PinError("the server's certificate key does not match the pinned SPKI")

    def wrap(self, raw: socket.socket, host: str) -> ssl.SSLSocket:
        """Handshake over `raw` and check the pins; closes it on failure."""
        try:
            sock = self.context().wrap_socket(raw, server_hostname=host)
        except (ssl.SSLError, ssl.CertificateError) as exc:
            raw.close()
            raise PinError(f"TLS handshake refused: {exc.__class__.__name__}") from exc
        try:
            self.verify(sock)
        except PinError:
            sock.close()
            raise
        return sock

    def connect(self, host: str, port: int, *, timeout=10.0) -> ssl.SSLSocket:
        raw = socket.create_connection((host, port), timeout=timeout)
        return self.wrap(raw, host)

    def document(self) -> dict:
        return {"ca_pem": self.ca_pem, "spki_sha256": self.spki_sha256}

    @classmethod
    def from_document(cls, body) -> "Pins":
        if not isinstance(body, dict) or set(body) != {"ca_pem", "spki_sha256"}:
            raise ValueError("tls pins need ca_pem and spki_sha256")
        ca, spki = body["ca_pem"], body["spki_sha256"]
        return cls(
            ca_pem=check_ca_pem(ca) if ca else None,
            spki_sha256=parse_spki_pin(spki) if spki else None,
        )
