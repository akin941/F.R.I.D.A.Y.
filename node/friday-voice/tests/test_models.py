"""models.json (brief D8): the shared wake/VAD entries equal the browser's
manifest; the bundled smart-turn model matches its pin; wake mode never
arms on an unpinned or mismatched file."""

import hashlib
import json

from conftest import REPO_ROOT

from friday_voice import models, pipecat_adapter

FRONTEND_MANIFEST = REPO_ROOT / "frontend" / "src" / "lib" / "voice" / "model-manifest.json"
SHARED_ROLES = ("melspectrogram", "embedding", "keyword", "vad")


def test_the_shared_entries_equal_the_frontend_manifest():
    node = models.by_role()
    browser = {e["role"]: e for e in json.loads(FRONTEND_MANIFEST.read_text("utf-8"))["models"]}
    for role in SHARED_ROLES:
        for key in ("file", "source", "sha256"):
            assert node[role][key] == browser[role][key], (role, key)
    assert models.load_manifest()["wakePhrase"] == "Hey Friday"


def test_every_entry_is_pinned_or_explicitly_unpinned():
    for entry in models.load_manifest()["models"]:
        assert set(entry) >= {"role", "file", "source", "sha256", "license"}
        assert entry["sha256"] == "" or len(entry["sha256"]) == 64
        assert entry["license"]
    assert models.by_role()["keyword"]["sha256"] == ""  # owner step: train hey_friday


def test_the_bundled_smart_turn_model_matches_its_pin():
    pinned = models.by_role()["smart_turn"]["sha256"]
    assert pipecat_adapter.bundled_model_sha256("smart_turn_v3") == pinned
    assert (
        models.by_role()["smart_turn"]["file"] == pipecat_adapter.BUNDLED_MODELS["smart_turn_v3"][1]
    )


def _manifest_with(tmp_path, contents):
    entries = []
    for role, data in contents.items():
        (tmp_path / f"{role}.onnx").write_bytes(data)
        entries.append(
            {"role": role, "file": f"{role}.onnx", "sha256": hashlib.sha256(data).hexdigest()}
        )
    return {"models": entries}


def test_wake_arms_only_when_every_wake_model_matches(tmp_path):
    manifest = _manifest_with(
        tmp_path, {"melspectrogram": b"mel", "embedding": b"emb", "keyword": b"kw"}
    )
    assert models.wake_armable(tmp_path, manifest)
    (tmp_path / "keyword.onnx").write_bytes(b"retrained")
    assert models.verify(tmp_path, manifest=manifest)["keyword"] == "mismatch"
    assert not models.wake_armable(tmp_path, manifest)
    (tmp_path / "keyword.onnx").unlink()
    assert models.verify(tmp_path, manifest=manifest)["keyword"] == "missing"


def test_an_unpinned_keyword_never_arms(tmp_path):
    manifest = _manifest_with(
        tmp_path, {"melspectrogram": b"mel", "embedding": b"emb", "keyword": b"kw"}
    )
    manifest["models"][2]["sha256"] = ""
    assert models.verify(tmp_path, manifest=manifest)["keyword"] == "unpinned"
    assert not models.wake_armable(tmp_path, manifest)


def test_the_real_manifest_never_arms_today(tmp_path):
    assert not models.wake_armable(tmp_path)
