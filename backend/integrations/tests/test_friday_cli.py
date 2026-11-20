"""integrations.cli.friday_cli unit tests -- no live server, no Django ORM
(this module is a standalone stdlib script). urllib.request.urlopen is
mocked; a _FakeResponse stands in for http.client.HTTPResponse."""

import io
import json
import urllib.error
from unittest import mock

import pytest

from integrations.cli import friday_cli


class _FakeResponse:
    """Context-manager + line-iterable + .read() -- the subset of
    http.client.HTTPResponse friday_cli actually uses."""

    def __init__(self, lines=(), body=b""):
        self._lines = list(lines)
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._body


def _sse_lines(*events):
    lines = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}\n".encode())
        lines.append(b"\n")
    return lines


# --- load_config ---


@pytest.fixture(autouse=True)
def _no_legacy_config(tmp_path, monkeypatch):
    """Keep the developer's real pre-rename config and env out of every test."""
    monkeypatch.setattr(friday_cli, "LEGACY_CONFIG_PATH", tmp_path / "legacy-missing.toml")
    monkeypatch.delenv("JARVIS_CLI_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_CLI_URL", raising=False)


def test_load_config_falls_back_to_the_legacy_jarvis_env_vars(tmp_path, monkeypatch):
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.delenv("FRIDAY_CLI_TOKEN", raising=False)
    monkeypatch.delenv("FRIDAY_CLI_URL", raising=False)
    monkeypatch.setenv("JARVIS_CLI_TOKEN", "jpat_legacy")
    monkeypatch.setenv("JARVIS_CLI_URL", "http://legacy/")

    config = friday_cli.load_config()

    assert config["token"] == "jpat_legacy"
    assert config["base_url"] == "http://legacy"


def test_load_config_prefers_the_friday_env_vars_over_the_legacy_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("FRIDAY_CLI_TOKEN", "jpat_new")
    monkeypatch.setenv("FRIDAY_CLI_URL", "http://new")
    monkeypatch.setenv("JARVIS_CLI_TOKEN", "jpat_legacy")
    monkeypatch.setenv("JARVIS_CLI_URL", "http://legacy")

    config = friday_cli.load_config()

    assert (config["token"], config["base_url"]) == ("jpat_new", "http://new")


def test_load_config_reads_the_legacy_config_file_when_the_new_one_is_missing(
    tmp_path, monkeypatch
):
    legacy = tmp_path / "legacy.toml"
    legacy.write_text('[cli]\ntoken = "jpat_old_file"\nbase_url = "http://old"\n')
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(friday_cli, "LEGACY_CONFIG_PATH", legacy)
    monkeypatch.delenv("FRIDAY_CLI_TOKEN", raising=False)
    monkeypatch.delenv("FRIDAY_CLI_URL", raising=False)

    config = friday_cli.load_config()

    assert (config["token"], config["base_url"]) == ("jpat_old_file", "http://old")


def test_load_config_prefers_env_vars_over_file(tmp_path, monkeypatch):
    config_file = tmp_path / "cli.toml"
    config_file.write_text('[cli]\ntoken = "jpat_from_file"\nbase_url = "http://file"\n')
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", config_file)
    monkeypatch.setenv("FRIDAY_CLI_TOKEN", "jpat_from_env")
    monkeypatch.setenv("FRIDAY_CLI_URL", "http://env")

    config = friday_cli.load_config()
    assert config["token"] == "jpat_from_env"
    assert config["base_url"] == "http://env"


def test_load_config_falls_back_to_file(tmp_path, monkeypatch):
    config_file = tmp_path / "cli.toml"
    config_file.write_text(
        '[cli]\ntoken = "jpat_from_file"\nbase_url = "http://file/"\nconversation_id = "c1"\n'
    )
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", config_file)
    monkeypatch.delenv("FRIDAY_CLI_TOKEN", raising=False)
    monkeypatch.delenv("FRIDAY_CLI_URL", raising=False)

    config = friday_cli.load_config()
    assert config["token"] == "jpat_from_file"
    assert config["base_url"] == "http://file"  # trailing slash stripped
    assert config["conversation_id"] == "c1"


def test_load_config_raises_without_a_token(tmp_path, monkeypatch):
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.delenv("FRIDAY_CLI_TOKEN", raising=False)
    monkeypatch.setenv("FRIDAY_CLI_URL", "http://env")

    with pytest.raises(friday_cli.CliConfigError):
        friday_cli.load_config()


def test_load_config_raises_without_a_base_url(tmp_path, monkeypatch):
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("FRIDAY_CLI_TOKEN", "jpat_x")
    monkeypatch.delenv("FRIDAY_CLI_URL", raising=False)

    with pytest.raises(friday_cli.CliConfigError):
        friday_cli.load_config()


# --- _iter_sse_events ---


def test_iter_sse_events_parses_multiple_events():
    lines = _sse_lines({"type": "chunk", "text": "Hi"}, {"type": "done", "message_id": "m1"})
    events = list(friday_cli._iter_sse_events(lines))
    assert events == [
        {"type": "chunk", "text": "Hi"},
        {"type": "done", "message_id": "m1"},
    ]


# --- create_conversation / send_message ---


def test_create_conversation_returns_the_new_id():
    fake = _FakeResponse(body=json.dumps({"id": "conv-1"}).encode())
    with mock.patch.object(friday_cli.urllib.request, "urlopen", return_value=fake) as urlopen:
        conversation_id = friday_cli.create_conversation("http://base", "jpat_x")

    assert conversation_id == "conv-1"
    request = urlopen.call_args[0][0]
    assert request.full_url == "http://base/api/v1/ai/conversations"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer jpat_x"


def test_send_message_writes_chunks_and_returns_done_event():
    fake = _FakeResponse(
        lines=_sse_lines(
            {"type": "chunk", "text": "Hel"},
            {"type": "chunk", "text": "lo"},
            {
                "type": "done",
                "message_id": "m1",
                "usage": {},
                "memories_used": [],
                "chunks_used": [],
            },
        )
    )
    out = io.StringIO()
    with mock.patch.object(friday_cli.urllib.request, "urlopen", return_value=fake):
        terminal = friday_cli.send_message("http://base", "jpat_x", "conv-1", "hi", out=out)

    assert out.getvalue() == "Hello"
    assert terminal["type"] == "done"


def test_send_message_returns_error_event_without_raising():
    fake = _FakeResponse(lines=_sse_lines({"type": "error", "error_type": "no_provider"}))
    out = io.StringIO()
    with mock.patch.object(friday_cli.urllib.request, "urlopen", return_value=fake):
        terminal = friday_cli.send_message("http://base", "jpat_x", "conv-1", "hi", out=out)

    assert terminal == {"type": "error", "error_type": "no_provider"}


def test_send_message_includes_client_local_date_in_the_request_body():
    """task_draft extraction only resolves relative due dates ("tomorrow")
    when client_local_date is supplied on the same endpoint the web client
    already sends it on (Codex review, branch 2 round 2)."""
    fake = _FakeResponse(
        lines=_sse_lines(
            {
                "type": "done",
                "message_id": "m1",
                "usage": {},
                "memories_used": [],
                "chunks_used": [],
            }
        )
    )
    with mock.patch.object(friday_cli.urllib.request, "urlopen", return_value=fake) as urlopen:
        friday_cli.send_message("http://base", "jpat_x", "conv-1", "hi", out=io.StringIO())

    request = urlopen.call_args[0][0]
    body = json.loads(request.data)
    assert body["content"] == "hi"
    assert body["client_local_date"] == friday_cli.date.today().isoformat()


# --- main() ---


def test_main_creates_conversation_when_none_configured(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("FRIDAY_CLI_TOKEN", "jpat_x")
    monkeypatch.setenv("FRIDAY_CLI_URL", "http://base")

    create_resp = _FakeResponse(body=json.dumps({"id": "conv-new"}).encode())
    message_resp = _FakeResponse(
        lines=_sse_lines(
            {"type": "chunk", "text": "hi"},
            {
                "type": "done",
                "message_id": "m1",
                "usage": {},
                "memories_used": [],
                "chunks_used": [],
            },
        )
    )
    with mock.patch.object(
        friday_cli.urllib.request, "urlopen", side_effect=[create_resp, message_resp]
    ):
        exit_code = friday_cli.main(["hello"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "conv-new" in captured.err
    assert "hi" in captured.out


def test_main_reuses_configured_conversation_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("FRIDAY_CLI_TOKEN", "jpat_x")
    monkeypatch.setenv("FRIDAY_CLI_URL", "http://base")
    monkeypatch.setenv("FRIDAY_CLI_CONVERSATION_ID", "")  # not read; config only via file
    monkeypatch.setattr(
        friday_cli,
        "load_config",
        lambda: {"token": "jpat_x", "base_url": "http://base", "conversation_id": "conv-existing"},
    )

    message_resp = _FakeResponse(
        lines=_sse_lines(
            {
                "type": "done",
                "message_id": "m1",
                "usage": {},
                "memories_used": [],
                "chunks_used": [],
            }
        )
    )
    with mock.patch.object(
        friday_cli.urllib.request, "urlopen", return_value=message_resp
    ) as urlopen:
        exit_code = friday_cli.main(["hello"])

    assert exit_code == 0
    assert urlopen.call_count == 1  # no create_conversation call
    captured = capsys.readouterr()
    assert "Created conversation" not in captured.err


def test_main_returns_1_when_config_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.delenv("FRIDAY_CLI_TOKEN", raising=False)
    monkeypatch.delenv("FRIDAY_CLI_URL", raising=False)

    exit_code = friday_cli.main(["hello"])
    assert exit_code == 1
    assert "No token configured" in capsys.readouterr().err


def test_main_returns_1_on_http_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("FRIDAY_CLI_TOKEN", "jpat_bad")
    monkeypatch.setenv("FRIDAY_CLI_URL", "http://base")
    monkeypatch.setattr(
        friday_cli,
        "load_config",
        lambda: {"token": "jpat_bad", "base_url": "http://base", "conversation_id": "conv-1"},
    )

    error = urllib.error.HTTPError("http://base/x", 401, "Unauthorized", hdrs=None, fp=None)
    with mock.patch.object(friday_cli.urllib.request, "urlopen", side_effect=error):
        exit_code = friday_cli.main(["hello"])

    assert exit_code == 1
    assert "HTTP 401" in capsys.readouterr().err


def test_main_returns_1_with_no_message_and_empty_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert friday_cli.main([]) == 1


def test_main_preserves_meaningful_stdin_whitespace(tmp_path, monkeypatch):
    """A piped message with meaningful leading/trailing whitespace (e.g. an
    indented code snippet) must reach the API unmodified -- only the single
    trailing newline the pipe itself adds is trimmed (Codex review, branch
    2 round 2)."""
    monkeypatch.setattr(friday_cli, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(
        friday_cli,
        "load_config",
        lambda: {"token": "jpat_x", "base_url": "http://base", "conversation_id": "conv-1"},
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("  def f():\n    return 1\n"))

    message_resp = _FakeResponse(
        lines=_sse_lines(
            {
                "type": "done",
                "message_id": "m1",
                "usage": {},
                "memories_used": [],
                "chunks_used": [],
            }
        )
    )
    with mock.patch.object(
        friday_cli.urllib.request, "urlopen", return_value=message_resp
    ) as urlopen:
        exit_code = friday_cli.main([])

    assert exit_code == 0
    body = json.loads(urlopen.call_args[0][0].data)
    assert body["content"] == "  def f():\n    return 1"
