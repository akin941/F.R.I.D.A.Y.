"""Phase 12 D13: no registries -- the `skills` package imports no HTTP
client and opens no socket. An import scan over every module of the
package (production code), and a socket guard around a full
upload -> approve -> match -> render flow."""

import ast
import socket
from pathlib import Path

import pytest

from core.channels import CHANNEL_WEB

PACKAGE = Path(__file__).resolve().parents[1]
FORBIDDEN_ROOTS = {
    "requests",
    "httpx",
    "urllib",
    "urllib3",
    "http",
    "aiohttp",
    "socket",
    "ssl",
    "ftplib",
    "smtplib",
    "websocket",
    "websockets",
    "mcp",
    "subprocess",
    "pycurl",
    "grpc",
}


def _production_modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "tests" in path.relative_to(PACKAGE).parts:
            continue
        yield path


def _imported_roots(path):
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0]


def test_no_http_client_or_socket_import():
    modules = list(_production_modules())
    assert any(p.name == "skill_md.py" for p in modules)
    for path in modules:
        bad = set(_imported_roots(path)) & FORBIDDEN_ROOTS
        assert not bad, f"{path.name} imports {sorted(bad)}"


@pytest.fixture
def socket_guard(monkeypatch):
    opened = []

    def refuse(*args, **kwargs):
        opened.append(args)
        raise AssertionError("the skills flow opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return opened


@pytest.mark.django_db
def test_a_full_flow_opens_no_socket(settings, socket_guard):
    from ai_core.models import ModelProvider
    from skills import match, services
    from skills.tests.helpers import make_owner, make_zip, personal_of, skill_text

    settings.AI_FAKE_PROVIDER = True
    ModelProvider.objects.create(
        name="fake-chat",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    owner = make_owner()
    workspace = personal_of(owner)
    data = make_zip([("SKILL.md", skill_text().encode()), ("references/a.md", b"# A\n")])
    parsed = services.parse_upload(data, filename="skill.zip")
    version = services.propose_from_owner(
        actor=owner, channel=CHANNEL_WEB, workspace=workspace, parsed=parsed, origin="upload"
    )
    services.approve_version(
        actor=owner, channel=CHANNEL_WEB, version=version, expected_sha256=version.content_sha256
    )
    selection = match.match_skill(
        actor=owner, workspace=workspace, content="PR yaz", voice=True, personal_data=False
    )
    assert selection.called
    forced = match.forced_selection(actor=owner, workspace=workspace, name="pr-yaz")
    match.render_skill_block(forced.version)
    assert socket_guard == []
