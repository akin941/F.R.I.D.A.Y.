"""The pinned detector models (Phase 11 brief D8): `models.json` at the
package root lists every ONNX file the child may load with its sha256.

`verify` hashes the files in a model directory; `wake_armable` is the
browser's rule carried over: wake mode may arm only when every wake model
(melspectrogram, embedding, keyword) is present and matches a non-empty
pin. It reads files and computes hashes only -- it never downloads.
"""

import hashlib
import json
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "models.json"
WAKE_ROLES = ("melspectrogram", "embedding", "keyword")


def load_manifest(path=MANIFEST_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def by_role(manifest=None) -> dict:
    manifest = manifest or load_manifest()
    return {entry["role"]: entry for entry in manifest["models"]}


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(model_dir, roles=None, manifest=None) -> dict:
    """{role: "ok" | "missing" | "mismatch" | "unpinned"} for each role."""
    entries = by_role(manifest)
    results = {}
    for role in roles or entries:
        entry = entries[role]
        path = Path(model_dir) / entry["file"]
        if not entry["sha256"]:
            results[role] = "unpinned"
        elif not path.is_file():
            results[role] = "missing"
        elif sha256_file(path) != entry["sha256"]:
            results[role] = "mismatch"
        else:
            results[role] = "ok"
    return results


def wake_armable(model_dir, manifest=None) -> bool:
    return all(state == "ok" for state in verify(model_dir, WAKE_ROLES, manifest).values())


class ModelNotArmable(RuntimeError):
    """A detector model is missing, unpinned or modified: it is not loaded."""


def path_of(model_dir, role, manifest=None) -> Path:
    return Path(model_dir) / by_role(manifest)[role]["file"]


def default_model_dir(manifest=None) -> Path:
    """The node's model directory (outside the repo; `make voice-models-node`
    -- `friday-voice models fetch` -- writes it)."""
    return Path((manifest or load_manifest())["modelDir"]).expanduser()
