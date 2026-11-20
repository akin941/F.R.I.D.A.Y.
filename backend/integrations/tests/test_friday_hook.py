"""`friday-hook` (Phase 8A brief D6): standard library only, allow-listed
fields, client-side scrubbing, never blocks or disturbs Claude Code (exit 0,
nothing on stdout/stderr), never leaks the token. No real Claude Code,
Keychain or network: `urlopen` and `security` are mocked."""

import ast
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from integrations.cli import friday_hook, vendor_scrubber

CLI_DIR = Path(friday_hook.__file__).resolve().parent
TOKEN = "jpat_" + "T0kenValueThatMustNeverBePrinted"
FAKE_KEY = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


class _Response:
    def __init__(self, status=202):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(friday_hook, "LOG_PATH", tmp_path / "state" / "friday-hook.log")
    monkeypatch.setattr(friday_hook, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("FRIDAY_HOOK_URL", "http://friday.test:8001/")
    monkeypatch.delenv("FRIDAY_HOOK_TOKEN", raising=False)


def _keychain(token=TOKEN, returncode=0):
    return mock.Mock(return_value=subprocess.CompletedProcess([], returncode, token + "\n", ""))


def _stdin(payload):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return io.BytesIO(data)


def _notification(message="Claude needs your permission to use Bash", **extra):
    return {
        "hook_event_name": "Notification",
        "session_id": "0b9f6a3e-2c7d-4f51-9a8e-1d2c3b4a5f60",
        "transcript_path": "/Users/o/.claude/projects/p/0b9f.jsonl",
        "cwd": "/Users/o/code/friday",
        "message": message,
        "notification_type": "permission_prompt",
        **extra,
    }


def _run(payload, opener, keychain=None):
    return friday_hook.main(_stdin(payload), opener=opener, keychain=keychain or _keychain())


def _sent(opener):
    request = opener.call_args.args[0]
    return request, json.loads(request.data)


# --- what is sent ---------------------------------------------------------------


def test_posts_one_allow_listed_scrubbed_event(capsys):
    opener = mock.Mock(return_value=_Response())
    payload = _notification(
        message=f"Run with token {FAKE_KEY}?", prompt="never sent", tool_input={"x": 1}
    )
    assert _run(payload, opener) == 0
    request, body = _sent(opener)
    assert request.full_url == "http://friday.test:8001/api/v1/memory/ingest/hook-events"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    (event,) = body["events"]
    assert set(event) == {
        "hook_event_name",
        "session_id",
        "cwd",
        "message",
        "notification_type",
        "occurred_at",
        "event_id",
    }
    assert FAKE_KEY not in event["message"]
    assert "[SECRET:" in event["message"]
    assert opener.call_args.kwargs["timeout"] <= friday_hook.DEADLINE_SECONDS
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_the_message_is_cut_after_scrubbing():
    event = friday_hook.build_event(_notification(message="x" * 150 + " " + FAKE_KEY + " y" * 300))
    assert len(event["message"]) <= friday_hook.MAX_MESSAGE_CHARS
    assert FAKE_KEY[:10] not in event["message"]


@pytest.mark.parametrize("name", ["UserPromptSubmit", "PreToolUse", "PostToolUse", None])
def test_other_events_are_not_sent(name):
    opener = mock.Mock(return_value=_Response())
    keychain = _keychain()
    assert _run({"hook_event_name": name, "prompt": "secret plan"}, opener, keychain) == 0
    opener.assert_not_called()
    keychain.assert_not_called()


@pytest.mark.parametrize("name", sorted(friday_hook.ALLOWED_EVENTS))
def test_each_allowed_event_is_sent(name):
    opener = mock.Mock(return_value=_Response())
    _run({"hook_event_name": name, "session_id": "s-1", "cwd": "/x"}, opener)
    assert _sent(opener)[1]["events"][0]["hook_event_name"] == name


# --- never blocks, never leaks --------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("http://friday.test", 500, "boom", {}, None),
        urllib.error.HTTPError("http://friday.test", 401, "nope", {}, None),
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
        OSError("network down"),
    ],
    ids=["500", "401", "urlerror", "timeout", "oserror"],
)
def test_server_errors_and_timeouts_exit_zero_and_log_one_line(error, capsys):
    opener = mock.Mock(side_effect=error)
    assert _run(_notification(), opener) == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    log = friday_hook.LOG_PATH.read_text()
    assert len(log.splitlines()) == 1
    assert TOKEN not in log
    assert "permission" not in log  # never the payload
    assert friday_hook.LOG_PATH.stat().st_mode & 0o777 == 0o600


def test_a_non_202_status_is_logged():
    _run(_notification(), mock.Mock(return_value=_Response(status=200)))
    assert "unexpected status 200" in friday_hook.LOG_PATH.read_text()


def test_no_token_in_the_keychain_exits_zero_without_posting(capsys):
    opener = mock.Mock()
    assert _run(_notification(), opener, keychain=_keychain(token="", returncode=44)) == 0
    opener.assert_not_called()
    assert "no token" in friday_hook.LOG_PATH.read_text()
    assert capsys.readouterr().out == ""


def test_keychain_unavailable_exits_zero(capsys):
    keychain = mock.Mock(side_effect=FileNotFoundError("security"))
    opener = mock.Mock()
    assert _run(_notification(), opener, keychain=keychain) == 0
    opener.assert_not_called()


def test_keychain_call_is_bounded_and_reads_the_friday_hook_service():
    keychain = _keychain()
    _run(_notification(), mock.Mock(return_value=_Response()), keychain=keychain)
    argv = keychain.call_args.args[0]
    assert argv == ["security", "find-generic-password", "-s", "friday-hook", "-w"]
    assert keychain.call_args.kwargs["timeout"] <= 1.0


def test_env_token_is_used_where_no_keychain_exists(monkeypatch):
    monkeypatch.setenv("FRIDAY_HOOK_TOKEN", TOKEN)
    keychain = _keychain()
    opener = mock.Mock(return_value=_Response())
    _run(_notification(), opener, keychain=keychain)
    keychain.assert_not_called()
    assert _sent(opener)[0].get_header("Authorization") == f"Bearer {TOKEN}"


def test_no_base_url_exits_zero(monkeypatch):
    monkeypatch.delenv("FRIDAY_HOOK_URL")
    opener = mock.Mock()
    assert _run(_notification(), opener) == 0
    opener.assert_not_called()
    assert "no base URL" in friday_hook.LOG_PATH.read_text()


def test_base_url_from_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("FRIDAY_HOOK_URL")
    config = tmp_path / "hook.toml"
    config.write_text('[hook]\nbase_url = "https://friday.example/"\n')
    monkeypatch.setattr(friday_hook, "CONFIG_PATH", config)
    opener = mock.Mock(return_value=_Response())
    _run(_notification(), opener)
    assert _sent(opener)[0].full_url.startswith("https://friday.example/api/v1/")


@pytest.mark.parametrize("raw", [b"not json", b"", b"x" * (friday_hook.MAX_STDIN_BYTES + 1)])
def test_bad_input_exits_zero_without_posting(raw, capsys):
    opener = mock.Mock()
    assert _run(raw, opener) == 0
    opener.assert_not_called()
    assert capsys.readouterr().out == ""


def test_an_unexpected_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(friday_hook, "build_event", mock.Mock(side_effect=RuntimeError(TOKEN)))
    assert _run(_notification(), mock.Mock()) == 0
    log = friday_hook.LOG_PATH.read_text()
    assert "RuntimeError" in log
    assert TOKEN not in log


# --- standalone, standard library only ------------------------------------------


def _imports(path):
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("filename", ["friday_hook.py", "_scrubber_vendored.py"])
def test_imports_only_the_standard_library(filename):
    names = _imports(CLI_DIR / filename) - {"integrations"}  # its own vendored sibling
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


def test_runs_standalone_without_django(tmp_path):
    """Copied next to its vendored scrubber and run in an isolated
    interpreter (`-I`: no project on the path, no DJANGO_SETTINGS_MODULE
    use): it loads, scrubs, and never imports Django."""
    for name in ("friday_hook.py", "_scrubber_vendored.py"):
        (tmp_path / name).write_text((CLI_DIR / name).read_text())
    script_path = str(tmp_path / "friday_hook.py")
    script = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('fh', {script_path!r})\n"
        "fh = importlib.util.module_from_spec(spec); spec.loader.exec_module(fh)\n"
        f"event = fh.build_event({{'hook_event_name': 'Notification', 'session_id': 's',"
        f" 'cwd': '/x', 'message': 'key {FAKE_KEY}'}})\n"
        "assert 'django' not in sys.modules, 'django imported'\n"
        "print(json.dumps(event))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=30, check=True
    )
    event = json.loads(result.stdout)
    assert FAKE_KEY not in event["message"]


def test_the_vendored_scrubber_is_identical_to_the_source_module():
    assert (CLI_DIR / "_scrubber_vendored.py").read_text() == vendor_scrubber.render(), (
        "integrations/cli/_scrubber_vendored.py is stale: run "
        "`uv run python -m integrations.cli.vendor_scrubber`"
    )
    assert vendor_scrubber.main(["--check"]) == 0


def test_the_vendored_scrubber_behaves_like_the_source():
    from integrations.cli import _scrubber_vendored
    from memory.ingestion import scrubber

    sample = f"mail a@b.co, key {FAKE_KEY}, iban TR330006100519786457841326"
    assert _scrubber_vendored.scrub_text(sample).text == scrubber.scrub_text(sample).text
