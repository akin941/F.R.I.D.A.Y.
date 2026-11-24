"""The child's only door to Pipecat (Phase 11 brief D7).

Pipecat's 0.x/1.x API churns (roadmap §c.8), so every Pipecat name the
child uses is listed in SURFACE and reached through this module only. A
snapshot of the installed version's signatures for those names lives in
`friday_voice/pipecat_surface.json` (`scripts/snapshot_pipecat.py`
writes it; `--check` fails when the installed Pipecat differs), and
`tests/test_pipecat_surface.py` exercises every function here against the
pinned version.

Branch 2 ships the empty surface: the pin, the snapshot machinery and the
proof that Pipecat core installs and imports on Linux without extras.
Branch 3 (`feat/p11-pipecat-pipeline`) adds the frame classes,
`FrameProcessor`, `Pipeline`, `PipelineTask`, the VAD analyzer interface
and the smart-turn analyzer here. Upgrades only in a dedicated
`chore/p11-pipecat-<version>` branch: bump the pin, regenerate the
snapshot, fix this adapter, run the replay and conformance suites.

Never listed here: `pipecat.services.*` (provider STT/LLM/TTS services) --
the child's speech and language steps are its own HTTP clients of Friday
(D8), and the import guard refuses those modules anywhere in the package.
"""

import dataclasses
import hashlib
import importlib
import importlib.metadata
import inspect
from importlib import resources

DISTRIBUTION = "pipecat-ai"
PINNED_VERSION = "1.15.0"

# (module, attribute) pairs the child uses. Empty until branch 3.
SURFACE: tuple[tuple[str, str], ...] = ()

# Model files the pinned wheel bundles and the child may load (models.json
# pins the same sha256; the child verifies before loading, brief D8).
BUNDLED_MODELS = {
    "smart_turn_v3": ("pipecat.audio.turn.smart_turn.data", "smart-turn-v3.2-cpu.onnx"),
}


def installed_version() -> str:
    return importlib.metadata.version(DISTRIBUTION)


def describe(module_name, attribute) -> dict:
    """A stable description of one Pipecat name: kind, signature, bases and
    (for dataclasses) field names."""
    target = getattr(importlib.import_module(module_name), attribute)
    if inspect.isclass(target):
        entry = {
            "kind": "class",
            "bases": [f"{base.__module__}.{base.__qualname__}" for base in target.__mro__[1:]],
            "init": _signature(target.__init__),
        }
        if dataclasses.is_dataclass(target):
            entry["fields"] = [field.name for field in dataclasses.fields(target)]
        entry["methods"] = {
            name: _signature(member)
            for name, member in sorted(vars(target).items())
            if not name.startswith("_") and inspect.isfunction(member)
        }
        return entry
    if callable(target):
        return {"kind": "function", "signature": _signature(target)}
    return {"kind": "value", "type": type(target).__name__}


def _signature(target) -> str:
    try:
        return str(inspect.signature(target))
    except (TypeError, ValueError):
        return "<no signature>"


def bundled_model_sha256(name) -> str:
    package, filename = BUNDLED_MODELS[name]
    return hashlib.sha256(resources.files(package).joinpath(filename).read_bytes()).hexdigest()


def snapshot(surface=SURFACE) -> dict:
    """The document `pipecat_surface.json` holds."""
    return {
        "distribution": DISTRIBUTION,
        "version": installed_version(),
        "surface": {f"{module}:{attribute}": describe(module, attribute) for module, attribute in surface},
        "bundled_models": {name: bundled_model_sha256(name) for name in sorted(BUNDLED_MODELS)},
    }
