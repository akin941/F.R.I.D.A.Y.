"""The install's CA file (Phase 11 brief D6, runbook B7 contract test 6).

By default the child verifies an `https://` Friday against the public CA
bundle (certifi). A server on this Mac behind a self-signed or internal
certificate (deploy/docker/compose.local-tls.yml) is in no public bundle,
so the install can record **one CA file that replaces the default bundle**:

    friday-voice ca set <pem> [--home <dir>]     copy it in and record it
    friday-voice ca clear [--home <dir>]         back to the default bundle
    friday-voice ca show [--home <dir>]          what is recorded

`ca set` copies the PEM into the install home (`server-ca.pem`, so the
child never reads the repo checkout, which may sit in a TCC-protected
folder such as ~/Desktop) and writes `tls.json` `{"ca_file": <path>}` next
to it. `friday-voice run` reads `tls.json` from the default install home at
start-up.

The CA file only chooses the trust anchors. It never relaxes anything:
certificate and hostname verification stay on, the credential's
`ca_sha256` pin is still checked on every connection (client.pin_hook), and
there is no option that disables either. The host and the server cannot
set it (no protocol frame, no environment variable). A `tls.json` that is
unreadable or malformed, or a recorded CA file that is missing, unreadable
or holds no certificate, fails closed: every request is refused with
`tls_ca_file_unreadable` and the default bundle is **not** used instead.
"""

import hashlib
import json
import os
import ssl
import sys
from pathlib import Path

DEFAULT_HOME = "~/Library/Application Support/Friday/voice"
CONFIG_NAME = "tls.json"
CA_COPY_NAME = "server-ca.pem"
MAX_CA_BYTES = 256 * 1024


class TlsConfigError(Exception):
    """The install's TLS config or CA file is unusable (fail closed)."""


def default_home() -> Path:
    return Path(DEFAULT_HOME).expanduser()


def installed_ca_file(home=None) -> str | None:
    """The CA file recorded in `<home>/tls.json`, or None when there is no
    `tls.json` (the default bundle). Raises TlsConfigError for a config
    that exists but cannot be used. The CA file itself is checked by
    `load_context` (and by `check_ca_file`)."""
    path = Path(home).expanduser() if home is not None else default_home()
    path = path / CONFIG_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TlsConfigError(f"{CONFIG_NAME} unreadable ({type(exc).__name__})") from exc
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise TlsConfigError(f"{CONFIG_NAME} is not JSON") from exc
    if not isinstance(data, dict) or set(data) != {"ca_file"}:
        raise TlsConfigError(f'{CONFIG_NAME} must be {{"ca_file": "<absolute path>"}}')
    ca_file = data["ca_file"]
    if not isinstance(ca_file, str) or not ca_file or not os.path.isabs(ca_file):
        raise TlsConfigError(f"{CONFIG_NAME} ca_file must be an absolute path")
    return ca_file


def load_context(ca_file=None) -> ssl.SSLContext:
    """The verifying TLS context: `ca_file` as the only trust store when
    given, else the default bundle. CERT_REQUIRED and hostname checking are
    `create_default_context`'s and are never turned off. Raises
    TlsConfigError when `ca_file` cannot be loaded or holds no
    certificate."""
    if not ca_file:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    try:
        context = ssl.create_default_context(cafile=str(ca_file))
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise TlsConfigError(f"CA file unusable ({type(exc).__name__})") from exc
    if not context.cert_store_stats().get("x509"):
        raise TlsConfigError("CA file holds no certificate")
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise TlsConfigError("verification is off")  # never: create_default_context's
    return context


def check_ca_file(ca_file) -> list[str]:
    """Validate a PEM CA file and return the DER sha256 (hex) of every
    certificate in it -- the values a credential's `ca_sha256` can pin."""
    path = Path(ca_file)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise TlsConfigError(f"CA file unreadable ({type(exc).__name__})") from exc
    if len(data) > MAX_CA_BYTES:
        raise TlsConfigError("CA file too large")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise TlsConfigError("CA file is not PEM") from exc
    if "PRIVATE KEY" in text:
        raise TlsConfigError("CA file contains a private key: pass the certificate only")
    load_context(path)
    fingerprints = []
    marker_begin, marker_end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    rest = text
    while marker_begin in rest:
        start = rest.index(marker_begin)
        end = rest.index(marker_end, start) + len(marker_end)
        der = ssl.PEM_cert_to_DER_cert(rest[start:end])
        fingerprints.append(hashlib.sha256(der).hexdigest())
        rest = rest[end:]
    if not fingerprints:
        raise TlsConfigError("CA file holds no certificate")
    return fingerprints


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def set_ca(source, home=None) -> tuple[Path, list[str]]:
    """Copy `source` into the install home and record it in `tls.json`."""
    fingerprints = check_ca_file(source)
    home = Path(home).expanduser() if home is not None else default_home()
    if not home.is_dir():
        raise TlsConfigError("install home missing: run `make friday-voice-install` first")
    target = (home / CA_COPY_NAME).resolve()
    _write_atomic(target, Path(source).read_bytes())
    check_ca_file(target)
    config = json.dumps({"ca_file": str(target)}, indent=2) + "\n"
    _write_atomic(home / CONFIG_NAME, config.encode("utf-8"))
    return target, fingerprints


def clear_ca(home=None) -> bool:
    """Remove `tls.json` and the copied CA (back to the default bundle)."""
    home = Path(home).expanduser() if home is not None else default_home()
    removed = False
    for name in (CONFIG_NAME, CA_COPY_NAME):
        try:
            (home / name).unlink()
            removed = True
        except FileNotFoundError:
            pass
    return removed


def main(args) -> int:
    home = args.home
    try:
        if args.ca_command == "set":
            target, fingerprints = set_ca(args.ca_file, home)
            print(f"recorded CA file: {target}")
            for fingerprint in fingerprints:
                print(f"certificate DER sha256: {fingerprint}")
            return 0
        if args.ca_command == "clear":
            print("cleared" if clear_ca(home) else "nothing recorded")
            return 0
        ca_file = installed_ca_file(home)
        if ca_file is None:
            print("no CA file recorded: the default (public) bundle")
            return 0
        print(f"CA file: {ca_file}")
        for fingerprint in check_ca_file(ca_file):
            print(f"certificate DER sha256: {fingerprint}")
        return 0
    except TlsConfigError as exc:
        print(f"friday-voice ca: {exc}", file=sys.stderr)
        return 1
