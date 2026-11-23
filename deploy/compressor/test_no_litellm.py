"""Build-time test of the `compressor` image (brief D11): Headroom imports and
compresses a fixture with `litellm`, `openai` and `anthropic` blocked.

Run in the image build (`python test_no_litellm.py`) and with pytest in a
venv holding requirements.lock.txt. Not part of the backend suite: the
backend never installs or imports Headroom. If this fails at an upgrade,
Headroom is not adopted at that version (the flag stays off) -- never
"fixed" by installing `litellm`.
"""

import importlib.abc
import importlib.util
import json
import os
import sys

BLOCKED = ("litellm", "openai", "anthropic")


class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".", 1)[0] in BLOCKED:
            raise ImportError(f"{name} is blocked in the compressor image")
        return None


def _install_blocker():
    if not any(isinstance(f, _Blocker) for f in sys.meta_path):
        sys.meta_path.insert(0, _Blocker())


def _fixture():
    rows = [
        {"id": i, "title": f"session {i}", "status": "done", "branch": f"feat/x-{i}"}
        for i in range(60)
    ]
    return [
        {"role": "assistant", "content": json.dumps(rows)},
        {"role": "assistant", "content": "Deploy log summary: every step passed. " * 40},
    ]


def test_blocked_packages_are_not_installed():
    for name in BLOCKED:
        assert importlib.util.find_spec(name) is None, f"{name} must not be installed"


def test_headroom_compresses_with_litellm_blocked():
    _install_blocker()
    for key in ("HEADROOM_OFFLINE", "HF_HUB_OFFLINE", "DO_NOT_TRACK"):
        os.environ.setdefault(key, "1")
    os.environ.setdefault("HEADROOM_TELEMETRY", "off")
    os.environ.setdefault("HEADROOM_BEACON", "off")
    import server

    messages = _fixture()
    result = server.headroom_compress(messages, "gpt-4o")
    assert len(result["messages"]) == len(messages)
    assert all(m["role"] == "assistant" for m in result["messages"])
    assert result["tokens_after"] <= result["tokens_before"]
    assert not [m for m in sys.modules if m.split(".", 1)[0] in BLOCKED]


if __name__ == "__main__":
    for _name, _test in sorted(globals().items()):
        if _name.startswith("test_") and callable(_test):
            _test()
            print(f"ok {_name}")
