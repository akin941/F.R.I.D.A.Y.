"""The install's CA file (Phase 11 brief D6, runbook B7 contract test 6).

By default the child verifies an `https://` Friday against the public CA
bundle (certifi). A server on this Mac behind a self-signed or internal
certificate (deploy/docker/compose.local-tls.yml) is in no public bundle,
so the install can record **one CA file that replaces the default bundle**:

    friday-voice ca set <pem> [--allow-ca] [--home <dir>]   copy it in and record it
    friday-voice ca clear [--home <dir>]                    back to the default bundle
    friday-voice ca show [--home <dir>]                     what is recorded

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
An empty CA file path is an error, never "the default bundle".

The voice pin is **not** the node's pairing pin. The credential's
`ca_sha256` is the DER sha256 (hex) of the top certificate of the served
chain -- for the self-signed gen-local-tls certificate, that certificate
itself -- and the Swift host computes it from the chain it sees.
`NODE_TLS_PIN_SHA256` (the FridayNode pairing pin) is the SPKI sha256 in
base64. `ca set` and `ca show` print both, labelled.

`ca set` writes only into an install home (`check_home`): an existing real
directory (not a symlink) owned by this user, never the user's home
directory, and either a friday-voice install (its pyproject.toml names
friday-voice) or a directory holding nothing but `tls.json` /
`server-ca.pem`. It refuses when either of those is a symlink, writes each
through a fresh `O_EXCL | O_NOFOLLOW` temporary (mode 0600) renamed into
place, and tightens the home to 0700 (the child runs as the same user). It
warns when the PEM holds more than one certificate, and refuses a
certificate that may act as a CA -- `CA:TRUE`, or no basicConstraints at
all (v1, or v3 without the extension) -- unless `--allow-ca` is given: a CA
in the trust store accepts every certificate it signs for the hostname, and
the pin then holds only if the host sends that CA's DER sha256 as the top
of the chain.
"""

import base64
import hashlib
import json
import os
import secrets
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOME = "~/Library/Application Support/Friday/voice"
CONFIG_NAME = "tls.json"
CA_COPY_NAME = "server-ca.pem"
MAX_CA_BYTES = 256 * 1024
FILE_MODE = 0o600
HOME_MODE = 0o700


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
    given, the default bundle only for None (an empty path is an error).
    CERT_REQUIRED and hostname checking are `create_default_context`'s and
    are never turned off. Raises TlsConfigError when `ca_file` cannot be
    loaded or holds no certificate."""
    if ca_file is None:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    if not str(ca_file):
        raise TlsConfigError("CA file path is empty")
    try:
        context = ssl.create_default_context(cafile=str(ca_file))
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise TlsConfigError(f"CA file unusable ({type(exc).__name__})") from exc
    if not context.cert_store_stats().get("x509"):
        raise TlsConfigError("CA file holds no certificate")
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise TlsConfigError("verification is off")  # never: create_default_context's
    return context


# --- describing the certificates (a minimal DER walk, no extra dependency) ---


@dataclass(frozen=True)
class CertInfo:
    """One certificate of a PEM file."""

    der_sha256: str  # hex: what a credential's `ca_sha256` pins (the voice pin)
    spki_sha256_b64: str  # base64: the node pairing pin / NODE_TLS_PIN_SHA256
    ca_constraint: bool | None  # basicConstraints cA; None: no extension (v1 or v3 without)

    @property
    def is_ca(self) -> bool:
        """True unless the certificate says CA:FALSE: without
        basicConstraints, OpenSSL may accept it as an issuer, so it counts
        as a possible CA."""
        return self.ca_constraint is not False

    @property
    def ca_label(self) -> str:
        if self.ca_constraint is None:
            return "no basicConstraints: possibly a CA"
        return "CA:TRUE" if self.ca_constraint else "CA:FALSE"


def _tlv(data: bytes, offset: int):
    """(tag, value_start, value_end) of the DER element at `offset`."""
    if offset + 2 > len(data):
        raise ValueError("truncated DER")
    tag, length = data[offset], data[offset + 1]
    start = offset + 2
    if length & 0x80:
        count = length & 0x7F
        if not 0 < count <= 4 or start + count > len(data):
            raise ValueError("bad DER length")
        length = int.from_bytes(data[start : start + count], "big")
        start += count
    end = start + length
    if end > len(data):
        raise ValueError("truncated DER")
    return tag, start, end


def _children(data: bytes, start: int, end: int):
    """[(offset, tag, value_start, value_end)] of the elements in a value."""
    out, offset = [], start
    while offset < end:
        tag, value_start, value_end = _tlv(data, offset)
        out.append((offset, tag, value_start, value_end))
        offset = value_end
    return out


_SEQUENCE, _BOOLEAN, _OID, _OCTET_STRING = 0x30, 0x01, 0x06, 0x04
_VERSION, _EXTENSIONS = 0xA0, 0xA3
_BASIC_CONSTRAINTS_OID = bytes([0x55, 0x1D, 0x13])  # 2.5.29.19


def _basic_constraints_ca(der: bytes, extensions_start: int, extensions_end: int):
    """basicConstraints cA: True / False, or None when the extension is
    absent."""
    (_off, tag, seq_start, seq_end), *_rest = _children(der, extensions_start, extensions_end)
    if tag != _SEQUENCE:
        raise ValueError("bad extensions")
    for _o, _t, ext_start, ext_end in _children(der, seq_start, seq_end):
        parts = _children(der, ext_start, ext_end)
        _oo, oid_tag, oid_start, oid_end = parts[0]
        if oid_tag != _OID or der[oid_start:oid_end] != _BASIC_CONSTRAINTS_OID:
            continue
        _ov, value_tag, value_start, value_end = parts[-1]
        if value_tag != _OCTET_STRING:
            raise ValueError("bad basicConstraints")
        bc_tag, bc_start, bc_end = _tlv(der, value_start)
        if bc_tag != _SEQUENCE:
            raise ValueError("bad basicConstraints")
        inner = _children(der, bc_start, bc_end)
        return bool(inner) and inner[0][1] == _BOOLEAN and der[inner[0][2]] != 0
    return None


def cert_info(der: bytes) -> CertInfo:
    """The two pins and the CA flag of one DER certificate: TBSCertificate's
    subjectPublicKeyInfo and the basicConstraints extension; nothing else
    is read."""
    try:
        _tag, cert_start, cert_end = _tlv(der, 0)
        _off, tbs_tag, tbs_start, tbs_end = _children(der, cert_start, cert_end)[0]
        if tbs_tag != _SEQUENCE:
            raise ValueError("no TBSCertificate")
        fields = _children(der, tbs_start, tbs_end)
        first = 1 if fields[0][1] == _VERSION else 0
        # serial, signature, issuer, validity, subject, subjectPublicKeyInfo
        spki_offset, spki_tag, _s, spki_end = fields[first + 5]
        if spki_tag != _SEQUENCE:
            raise ValueError("no subjectPublicKeyInfo")
        constraint = None  # v1, or v3 without basicConstraints: unknown
        for _o, tag, start, end in fields[first + 6 :]:
            if tag == _EXTENSIONS:
                constraint = _basic_constraints_ca(der, start, end)
    except (ValueError, IndexError) as exc:
        raise TlsConfigError("CA file holds a certificate that cannot be parsed") from exc
    spki = der[spki_offset:spki_end]
    return CertInfo(
        der_sha256=hashlib.sha256(der).hexdigest(),
        spki_sha256_b64=base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii"),
        ca_constraint=constraint,
    )


def check_ca_file(ca_file) -> list[CertInfo]:
    """Validate a PEM CA file (certificates only, loadable as a trust
    store) and describe every certificate in it."""
    if ca_file is None or not str(ca_file):
        raise TlsConfigError("CA file path is empty")
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
    infos = []
    marker_begin, marker_end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    rest = text
    while marker_begin in rest:
        start = rest.index(marker_begin)
        end = rest.index(marker_end, start) + len(marker_end)
        infos.append(cert_info(ssl.PEM_cert_to_DER_cert(rest[start:end])))
        rest = rest[end:]
    if not infos:
        raise TlsConfigError("CA file holds no certificate")
    return infos


def bundle_warnings(infos) -> list[str]:
    warnings = []
    if len(infos) > 1:
        warnings.append(
            f"the PEM holds {len(infos)} certificates: every one becomes a trust anchor, "
            "and the pin must name the top certificate of the chain the server sends"
        )
    for number, info in enumerate(infos, 1):
        if info.is_ca:
            warnings.append(
                f"certificate {number} is treated as a CA ({info.ca_label}): it may trust "
                "every certificate it signs for the hostname; the pin holds only if the "
                "host sends this certificate's DER sha256 as the top of the chain"
            )
    return warnings


# --- writing the record ---


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise TlsConfigError(f"{path.name} in the install home is a symlink: not written")


def _write_atomic(path: Path, data: bytes) -> None:
    """Write `data` to `path` (mode 0600) through a fresh O_EXCL|O_NOFOLLOW
    temporary in the same directory, renamed into place. rename() replaces
    the directory entry and never follows a link at `path`."""
    _refuse_symlink(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, FILE_MODE, follow_symlinks=False)  # whatever the umask was
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


RECORD_NAMES = frozenset({CONFIG_NAME, CA_COPY_NAME})


def _is_voice_install(home: Path) -> bool:
    """A `make friday-voice-install` home: the rsynced package, whose
    pyproject.toml names the friday-voice project."""
    import tomllib

    pyproject = home / "pyproject.toml"
    if pyproject.is_symlink() or not pyproject.is_file() or not (home / "friday_voice").is_dir():
        return False
    try:
        data = tomllib.loads(pyproject.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    return data.get("project", {}).get("name") == "friday-voice"


def check_home(home) -> Path:
    """The directory `ca set` may write into and chmod 0700, or
    TlsConfigError. The rule: an existing real directory (not a symlink),
    owned by this user, never the user's home directory, and either a
    friday-voice install (`make friday-voice-install`) or a directory
    holding nothing but the two record files (fresh, or a previous
    `ca set`)."""
    home = Path(home).expanduser() if home is not None else default_home()
    if home.is_symlink():
        raise TlsConfigError("the install home is a symlink: not written")
    if not home.is_dir():
        raise TlsConfigError("install home missing: run `make friday-voice-install` first")
    home = home.absolute()
    if home.resolve() == Path.home().resolve() or home.resolve() == Path("/"):
        raise TlsConfigError("refusing the user's home directory as the install home")
    if home.stat().st_uid != os.getuid():
        raise TlsConfigError("the install home is not owned by this user: not written")
    entries = {entry.name for entry in home.iterdir()}
    if not entries <= RECORD_NAMES and not _is_voice_install(home):
        raise TlsConfigError(
            "the install home is neither a friday-voice install nor an empty directory: not written"
        )
    return home


def set_ca(source, home=None, *, allow_ca=False):
    """Copy `source` into the install home and record it in `tls.json`.
    Returns `(target, infos, warnings)`."""
    infos = check_ca_file(source)
    if any(info.is_ca for info in infos) and not allow_ca:
        labels = ", ".join(sorted({info.ca_label for info in infos if info.is_ca}))
        raise TlsConfigError(
            f"the PEM holds a certificate that may act as a CA ({labels}), which trusts "
            "every certificate it signs: pass --allow-ca to record it deliberately (the "
            "gen-local-tls certificate is CA:FALSE)"
        )
    home = check_home(home)
    target, config_path = home / CA_COPY_NAME, home / CONFIG_NAME
    for path in (target, config_path):
        _refuse_symlink(path)
    os.chmod(home, HOME_MODE, follow_symlinks=False)
    _write_atomic(target, Path(source).read_bytes())
    check_ca_file(target)
    config = json.dumps({"ca_file": str(target)}, indent=2) + "\n"
    _write_atomic(config_path, config.encode("utf-8"))
    return target, infos, bundle_warnings(infos)


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


def _print_infos(infos) -> None:
    for number, info in enumerate(infos, 1):
        print(f"certificate {number} of {len(infos)} ({info.ca_label}):")
        print(f"  voice pin (credential ca_sha256, DER sha256 hex): {info.der_sha256}")
        print(
            "  node pairing pin (SPKI sha256 base64, NODE_TLS_PIN_SHA256; "
            f"not the voice pin): {info.spki_sha256_b64}"
        )


def _warn(warnings) -> None:
    for warning in warnings:
        print(f"friday-voice ca: warning: {warning}", file=sys.stderr)


def main(args) -> int:
    home = args.home
    try:
        if args.ca_command == "set":
            target, infos, warnings = set_ca(args.ca_file, home, allow_ca=args.allow_ca)
            _warn(warnings)
            print(f"recorded CA file: {target}")
            _print_infos(infos)
            return 0
        if args.ca_command == "clear":
            print("cleared" if clear_ca(home) else "nothing recorded")
            return 0
        ca_file = installed_ca_file(home)
        if ca_file is None:
            print("no CA file recorded: the default (public) bundle")
            return 0
        infos = check_ca_file(ca_file)
        _warn(bundle_warnings(infos))
        print(f"CA file: {ca_file}")
        _print_infos(infos)
        return 0
    except TlsConfigError as exc:
        print(f"friday-voice ca: {exc}", file=sys.stderr)
        return 1
