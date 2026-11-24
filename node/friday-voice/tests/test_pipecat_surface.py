"""Pipecat (brief D7): the exact pin, the surface snapshot and every adapter
function against the installed version; Pipecat core installs and imports
on Linux without provider extras."""

import importlib
import importlib.metadata
import importlib.util
import tomllib

import pytest
from conftest import PACKAGE_ROOT, load_script

from friday_voice import pipecat_adapter

snapshot_script = load_script("snapshot_pipecat")


def test_the_pin_is_exact_and_installed():
    project = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text("utf-8"))
    dependencies = project["project"]["dependencies"]
    assert f"pipecat-ai=={pipecat_adapter.PINNED_VERSION}" in dependencies
    assert all("==" in dep for dep in dependencies), "exact pins only (D7)"
    assert not any("[" in dep for dep in dependencies), "no extras (D7)"
    assert pipecat_adapter.installed_version() == pipecat_adapter.PINNED_VERSION


def test_the_snapshot_matches_the_installed_pipecat():
    assert snapshot_script.main(["--check"]) == 0


def test_the_snapshot_check_fails_on_a_difference(tmp_path, monkeypatch):
    stale = tmp_path / "pipecat_surface.json"
    stale.write_text(snapshot_script.render().replace("1.15.0", "1.14.0"), encoding="utf-8")
    monkeypatch.setattr(snapshot_script, "TARGET", stale)
    assert snapshot_script.main(["--check"]) == 1


@pytest.mark.parametrize(
    "module",
    [
        "pipecat.frames.frames",
        "pipecat.processors.frame_processor",
        "pipecat.pipeline.pipeline",
        "pipecat.pipeline.task",
        "pipecat.audio.vad.vad_analyzer",
        "pipecat.audio.turn.smart_turn.local_smart_turn_v3",
    ],
)
def test_pipecat_core_imports_on_linux_without_extras(module):
    assert importlib.import_module(module)


def test_no_provider_extra_is_installed():
    # pipecat-ai core itself requires the `openai` SDK (recorded in the
    # dependency manifest); the child never imports it (test_guards.py).
    for name in ("anthropic", "google.genai", "deepgram", "elevenlabs", "cartesia", "litellm"):
        try:
            found = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            found = None
        assert found is None, name


def test_describe_a_class_and_a_function():
    frame = pipecat_adapter.describe("pipecat.frames.frames", "InputAudioRawFrame")
    assert frame["kind"] == "class"
    assert "audio" in frame["fields"] and "sample_rate" in frame["fields"]
    processor = pipecat_adapter.describe("pipecat.processors.frame_processor", "FrameProcessor")
    assert processor["kind"] == "class" and "process_frame" in processor["methods"]
    function = pipecat_adapter.describe("importlib", "import_module")
    assert function["kind"] == "function" and "name" in function["signature"]
    assert pipecat_adapter.describe("pipecat_adapter_probe", "VALUE")["kind"] == "value"


def test_snapshot_is_deterministic_and_covers_the_surface():
    first = pipecat_adapter.snapshot()
    assert first == pipecat_adapter.snapshot()
    assert set(first["surface"]) == {f"{m}:{a}" for m, a in pipecat_adapter.SURFACE}
    probe = pipecat_adapter.snapshot((("pipecat.pipeline.pipeline", "Pipeline"),))
    assert probe["surface"]["pipecat.pipeline.pipeline:Pipeline"]["kind"] == "class"


@pytest.fixture(autouse=True)
def _probe_module(monkeypatch):
    import sys
    import types

    probe = types.ModuleType("pipecat_adapter_probe")
    probe.VALUE = 3
    monkeypatch.setitem(sys.modules, "pipecat_adapter_probe", probe)
