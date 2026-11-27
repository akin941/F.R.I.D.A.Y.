"""`friday-voice models fetch` (`make voice-models-node`, runbook A10): fill
the node's model directory from `models.json`.

- every entry whose `source` is a URL is downloaded (https only; every
  redirect hop is checked before it is followed), hashed while it streams, and written only when the
  hash equals its non-empty pin;
- the owner-trained keyword model (`source: null`) is copied from a local
  path (`VOICE_WAKE_MODEL_SRC`) under the same rule. While its pin is empty
  the file is refused and its hash printed: pin it in `models.json` and the
  frontend manifest (a reviewed change), then rerun;
- `smart_turn` ships inside the pinned pipecat-ai wheel: it is never
  fetched, only checked against its pin.

Every write goes to a temporary file in the target directory and is moved
into place with `os.replace` after verification, so the directory never
holds a partial or unverified model. A file already present and matching
its pin is left alone. Nothing here runs at child start-up; the child only
reads and hashes (`friday_voice.models`).
"""

import hashlib
import ipaddress
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from . import models

MAX_MODEL_BYTES = 64 * 1024 * 1024
CHUNK = 1 << 16
MAX_REDIRECTS = 5
BUNDLED_ROLES = {"smart_turn": "smart_turn_v3"}


class FetchRefused(RuntimeError):
    """A model was not written: bad source, failed download or hash mismatch."""


def is_url(source) -> bool:
    return isinstance(source, str) and urlsplit(source).scheme in ("https", "http")


def _is_loopback(host) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url, *, allow_loopback_http=False) -> None:
    parts = urlsplit(str(url))
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and allow_loopback_http and _is_loopback(parts.hostname or ""):
        return  # tests only: a fixture server on 127.0.0.1
    raise FetchRefused(f"source must be https: {url}")


def _write_verified(model_dir: Path, entry, chunks) -> int:
    """Stream `chunks` into a temp file in `model_dir`, then move it over
    `entry['file']` only if its sha256 equals the pin. Returns the size."""
    expected = entry["sha256"]
    digest = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(prefix=f".{entry['file']}.", suffix=".part", dir=model_dir)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in chunks:
                size += len(chunk)
                if size > MAX_MODEL_BYTES:
                    raise FetchRefused(f"{entry['file']}: larger than {MAX_MODEL_BYTES} bytes")
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        actual = digest.hexdigest()
        if not expected:
            raise FetchRefused(
                f"{entry['file']}: sha256 not pinned in models.json (this file hashes to "
                f"{actual}); pin it there and in the frontend manifest, then rerun"
            )
        if actual != expected:
            raise FetchRefused(
                f"{entry['file']}: sha256 mismatch (expected {expected}, got {actual})"
            )
        os.chmod(tmp, 0o644)
        os.replace(tmp, model_dir / entry["file"])
    finally:
        tmp.unlink(missing_ok=True)
    return size


def _download_chunks(url, *, client, allow_loopback_http):
    """Follow up to MAX_REDIRECTS redirects by hand (GitHub release assets
    redirect), checking every hop before it is requested: none may leave
    https."""
    import httpx

    current = httpx.URL(url)
    for _hop in range(MAX_REDIRECTS + 1):
        check_url(current, allow_loopback_http=allow_loopback_http)
        with client.stream("GET", current) as response:
            if response.is_redirect:
                current = current.join(response.headers["location"])
                continue
            if response.status_code != 200:
                raise FetchRefused(f"download failed ({response.status_code}): {url}")
            yield from response.iter_bytes(CHUNK)
            return
    raise FetchRefused(f"too many redirects: {url}")


def _file_chunks(path):
    with open(path, "rb") as handle:
        yield from iter(lambda: handle.read(CHUNK), b"")


def _http_client():
    import httpx

    return httpx.Client(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(30.0, connect=10.0),
    )


def fetch(
    model_dir=None,
    *,
    keyword_src=None,
    manifest=None,
    client=None,
    allow_loopback_http=False,
    out=print,
) -> list:
    """Fill `model_dir`. Returns one `(role, status, detail)` per manifest
    entry; status is ok | fetched | copied | bundled | skipped | failed."""
    manifest = manifest or models.load_manifest()
    model_dir = Path(model_dir) if model_dir is not None else models.default_model_dir(manifest)
    model_dir = model_dir.expanduser()
    model_dir.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    results = []
    try:
        for entry in manifest["models"]:
            role, name, source = entry["role"], entry["file"], entry.get("source")
            try:
                if role in BUNDLED_ROLES:
                    from . import pipecat_adapter

                    actual = pipecat_adapter.bundled_model_sha256(BUNDLED_ROLES[role])
                    if not entry["sha256"] or actual != entry["sha256"]:
                        raise FetchRefused(f"{name}: the bundled copy does not match its pin")
                    result = (role, "bundled", f"{name} (inside the pipecat-ai wheel, verified)")
                elif models.verify(model_dir, (role,), manifest)[role] == "ok":
                    size = (model_dir / name).stat().st_size
                    result = (role, "ok", f"{name} {size} bytes (already verified)")
                elif is_url(source):
                    if client is None:
                        client = _http_client()
                    chunks = _download_chunks(
                        source, client=client, allow_loopback_http=allow_loopback_http
                    )
                    size = _write_verified(model_dir, entry, chunks)
                    result = (role, "fetched", f"{name} {size} bytes sha256 {entry['sha256']}")
                elif source is None:
                    if not keyword_src:
                        result = (
                            role,
                            "skipped",
                            f"{name}: owner-provided; set VOICE_WAKE_MODEL_SRC=/path/to/{name}",
                        )
                    else:
                        src = Path(keyword_src).expanduser()
                        if not src.is_file():
                            raise FetchRefused(f"{name}: VOICE_WAKE_MODEL_SRC is not a file")
                        size = _write_verified(model_dir, entry, _file_chunks(src))
                        result = (role, "copied", f"{name} {size} bytes sha256 {entry['sha256']}")
                else:
                    raise FetchRefused(f"{name}: unsupported source {source!r}")
            except FetchRefused as exc:
                result = (role, "failed", str(exc))
            except Exception as exc:  # noqa: BLE001 -- a network error fails this file only
                result = (role, "failed", f"{name}: {type(exc).__name__}: {exc}")
            results.append(result)
            out(f"{result[1]:<8} {result[0]:<15} {result[2]}")
    finally:
        if own_client and client is not None:
            client.close()
    roles = models.by_role(manifest)
    armable = all(r in roles for r in models.WAKE_ROLES) and models.wake_armable(
        model_dir, manifest
    )
    out(f"model dir: {model_dir}")
    out("wake mode: armable" if armable else "wake mode: not armable (push-to-talk only)")
    return results


def main(args) -> int:
    keyword_src = args.keyword_src or os.environ.get("VOICE_WAKE_MODEL_SRC") or None
    results = fetch(args.model_dir, keyword_src=keyword_src)
    return 1 if any(status == "failed" for _, status, _ in results) else 0
