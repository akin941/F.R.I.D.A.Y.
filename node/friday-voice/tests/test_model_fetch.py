"""`friday-voice models fetch` (runbook A10): URL entries are downloaded and
written only when their sha256 matches the pin, the owner's keyword model is
copied under the same rule, writes are atomic, and the real `models.json`
is what `make voice-models-node` reads. No network: a fixture server on
127.0.0.1 serves the files."""

import hashlib
import http.server
import threading

import pytest
from conftest import REPO_ROOT

from friday_voice import cli, model_fetch, models


def _sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def server():
    """A loopback HTTP server: `files[path] = bytes`, `/redirect/<p>` -> `<p>`."""
    files = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/redirect/"):
                self.send_response(302)
                self.send_header("Location", self.path[len("/redirect") :])
                self.end_headers()
                return
            if self.path.startswith("/to-https/"):
                self.send_response(302)
                self.send_header("Location", "http://example.invalid/x.onnx")
                self.end_headers()
                return
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, files
    httpd.shutdown()
    httpd.server_close()


def _manifest(base, entries):
    return {"modelDir": "unused", "models": entries}


def _entry(role, file, source, sha):
    return {"role": role, "file": file, "source": source, "sha256": sha, "license": "test"}


def _fetch(tmp_path, manifest, **kwargs):
    lines = []
    results = model_fetch.fetch(
        tmp_path / "models",
        manifest=manifest,
        allow_loopback_http=True,
        out=lines.append,
        **kwargs,
    )
    return {role: (status, detail) for role, status, detail in results}, lines


def test_url_entries_are_downloaded_verified_and_written(tmp_path, server):
    base, files = server
    files["/mel.onnx"] = b"mel-bytes"
    files["/emb.onnx"] = b"emb-bytes"
    manifest = _manifest(
        base,
        [
            _entry("melspectrogram", "mel.onnx", f"{base}/mel.onnx", _sha(b"mel-bytes")),
            _entry("embedding", "emb.onnx", f"{base}/redirect/emb.onnx", _sha(b"emb-bytes")),
        ],
    )
    results, _ = _fetch(tmp_path, manifest)
    assert results["melspectrogram"][0] == "fetched"
    assert results["embedding"][0] == "fetched"  # followed the redirect
    assert (tmp_path / "models" / "mel.onnx").read_bytes() == b"mel-bytes"
    assert models.verify(tmp_path / "models", manifest=manifest) == {
        "melspectrogram": "ok",
        "embedding": "ok",
    }
    again, _ = _fetch(tmp_path, manifest)
    assert again["melspectrogram"][0] == "ok"  # already verified: not downloaded again


def test_a_mismatched_download_is_refused_and_leaves_nothing(tmp_path, server):
    base, files = server
    files["/vad.onnx"] = b"tampered"
    manifest = _manifest(base, [_entry("vad", "vad.onnx", f"{base}/vad.onnx", _sha(b"good"))])
    results, _ = _fetch(tmp_path, manifest)
    assert results["vad"][0] == "failed"
    assert "sha256 mismatch" in results["vad"][1]
    assert list((tmp_path / "models").iterdir()) == []  # no file, no temp file


def test_a_mismatch_never_replaces_a_previous_file(tmp_path, server):
    base, files = server
    files["/vad.onnx"] = b"tampered"
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "vad.onnx").write_bytes(b"old")
    manifest = _manifest(base, [_entry("vad", "vad.onnx", f"{base}/vad.onnx", _sha(b"good"))])
    results, _ = _fetch(tmp_path, manifest)
    assert results["vad"][0] == "failed"
    assert (tmp_path / "models" / "vad.onnx").read_bytes() == b"old"
    assert [p.name for p in (tmp_path / "models").iterdir()] == ["vad.onnx"]


def test_http_errors_oversize_and_insecure_sources_fail(tmp_path, server, monkeypatch):
    base, files = server
    files["/big.onnx"] = b"x" * 2048
    monkeypatch.setattr(model_fetch, "MAX_MODEL_BYTES", 1024)
    manifest = _manifest(
        base,
        [
            _entry("melspectrogram", "missing.onnx", f"{base}/missing.onnx", _sha(b"a")),
            _entry("embedding", "big.onnx", f"{base}/big.onnx", _sha(b"x" * 2048)),
            _entry("vad", "off.onnx", f"{base}/to-https/off.onnx", _sha(b"a")),
        ],
    )
    results, _ = _fetch(tmp_path, manifest)
    assert results["melspectrogram"] == ("failed", f"download failed (404): {base}/missing.onnx")
    assert "larger than 1024 bytes" in results["embedding"][1]
    assert "source must be https" in results["vad"][1]  # the redirect left loopback
    assert list((tmp_path / "models").iterdir()) == []


def test_plain_http_is_refused_without_the_test_flag(tmp_path, server):
    base, files = server
    files["/a.onnx"] = b"a"
    manifest = _manifest(base, [_entry("vad", "a.onnx", f"{base}/a.onnx", _sha(b"a"))])
    results = model_fetch.fetch(tmp_path, manifest=manifest, out=lambda _line: None)
    assert results == [("vad", "failed", f"source must be https: {base}/a.onnx")]
    with pytest.raises(model_fetch.FetchRefused):
        model_fetch.check_url("http://10.0.0.1/a.onnx", allow_loopback_http=True)
    model_fetch.check_url("https://github.com/a.onnx")


def test_the_keyword_model_is_copied_only_when_pinned(tmp_path):
    src = tmp_path / "hey_friday.onnx"
    src.write_bytes(b"owner-trained")
    pinned = _manifest("", [_entry("keyword", "hey_friday.onnx", None, _sha(b"owner-trained"))])
    unpinned = _manifest("", [_entry("keyword", "hey_friday.onnx", None, "")])

    skipped, _ = _fetch(tmp_path, pinned)
    assert skipped["keyword"][0] == "skipped"
    assert "VOICE_WAKE_MODEL_SRC" in skipped["keyword"][1]

    refused, _ = _fetch(tmp_path, unpinned, keyword_src=str(src))
    assert refused["keyword"][0] == "failed"
    assert _sha(b"owner-trained") in refused["keyword"][1]  # the hash to pin
    assert not (tmp_path / "models" / "hey_friday.onnx").exists()

    missing, _ = _fetch(tmp_path, pinned, keyword_src=str(tmp_path / "nope.onnx"))
    assert missing["keyword"][0] == "failed"

    copied, _ = _fetch(tmp_path, pinned, keyword_src=str(src))
    assert copied["keyword"][0] == "copied"
    assert (tmp_path / "models" / "hey_friday.onnx").read_bytes() == b"owner-trained"


def test_a_full_fill_makes_wake_mode_armable(tmp_path, server):
    base, files = server
    files["/mel.onnx"], files["/emb.onnx"] = b"mel", b"emb"
    src = tmp_path / "kw.onnx"
    src.write_bytes(b"kw")
    manifest = _manifest(
        base,
        [
            _entry("melspectrogram", "mel.onnx", f"{base}/mel.onnx", _sha(b"mel")),
            _entry("embedding", "emb.onnx", f"{base}/emb.onnx", _sha(b"emb")),
            _entry("keyword", "hey_friday.onnx", None, _sha(b"kw")),
        ],
    )
    _, lines = _fetch(tmp_path, manifest, keyword_src=str(src))
    assert lines[-1] == "wake mode: armable"
    assert models.wake_armable(tmp_path / "models", manifest)


def test_the_bundled_smart_turn_model_is_checked_not_fetched(tmp_path):
    manifest = {"models": [models.by_role()["smart_turn"]]}
    results, _ = _fetch(tmp_path, manifest)
    assert results["smart_turn"][0] == "bundled"
    assert list((tmp_path / "models").iterdir()) == []


def test_the_real_manifest_is_what_the_fill_reads():
    manifest = models.load_manifest()
    for entry in manifest["models"]:
        if entry["role"] in model_fetch.BUNDLED_ROLES:
            assert not model_fetch.is_url(entry["source"])
        elif entry["source"] is None:
            assert entry["role"] == "keyword"
        else:
            model_fetch.check_url(entry["source"])  # every download is https
            assert entry["sha256"]
    assert str(models.default_model_dir()).endswith(
        "Library/Application Support/Friday/voice/models"
    )


def test_the_cli_and_make_target_wire_through(tmp_path, monkeypatch):
    calls = {}

    def fake_fetch(model_dir, *, keyword_src=None):
        calls.update(model_dir=model_dir, keyword_src=keyword_src)
        return [("vad", "fetched", "")]

    monkeypatch.setattr(model_fetch, "fetch", fake_fetch)
    monkeypatch.setenv("VOICE_WAKE_MODEL_SRC", "/owner/hey_friday.onnx")
    assert cli.main(["models", "fetch", "--model-dir", str(tmp_path)]) == 0
    assert calls == {"model_dir": str(tmp_path), "keyword_src": "/owner/hey_friday.onnx"}

    monkeypatch.setattr(model_fetch, "fetch", lambda *_a, **_k: [("vad", "failed", "x")])
    assert cli.main(["models", "fetch"]) == 1

    makefile = (REPO_ROOT / "Makefile").read_text("utf-8")
    assert "voice-models-node:" in makefile
    assert "friday-voice models fetch" in makefile
    assert "--exclude /models/" in makefile  # a reinstall keeps the filled directory
