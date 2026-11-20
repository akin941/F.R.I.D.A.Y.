"""Phase 8A branch 6 (brief D12): the `coding_sessions_status` read tool --
role x channel (web only, human installation owner), `for_actor()`
isolation (another workspace's sessions are never listed), fenced output,
one audit row per read (ids and counts, never text)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import User
from accounts.services import register_user
from audit.models import AuditLog
from core.channels import (
    CHANNEL_CLI,
    CHANNEL_OPENAI,
    CHANNEL_SYSTEM,
    CHANNEL_TELEGRAM,
    CHANNEL_UNKNOWN,
    CHANNEL_WEB,
)
from memory.ingestion.services import ensure_default_sources, forget_source
from memory.models import CodingSession, IngestionSource
from tools.coding_sessions import TOOL_NAME, read_coding_sessions
from tools.owner_reads import AUDIT_ACTION
from tools.registry import ReadToolError, get_read_tool, list_read_tools
from workspaces.models import Workspace, WorkspaceMember
from workspaces.services import create_firm_workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def hooks(owner, personal):
    ensure_default_sources(workspace=personal, created_by=owner)
    source = IngestionSource.objects.get(
        workspace=personal, kind=IngestionSource.Kind.CLAUDE_CODE_HOOKS
    )
    source.enabled = True
    source.save()
    return source


def _session(workspace, source, session_id, *, project="friday", notification="", **extra):
    now = timezone.now()
    return CodingSession.objects.create(
        workspace=workspace,
        source=source,
        session_id=session_id,
        project=project,
        started_at=extra.pop("started_at", now),
        last_event_at=extra.pop("last_event_at", now),
        last_event="SessionStart",
        last_notification=notification,
        **extra,
    )


def _read(workspace, actor, channel=CHANNEL_WEB, args=None):
    return read_coding_sessions(
        workspace=workspace, actor=actor, args=args if args is not None else {}, channel=channel
    )


def _audits():
    return list(AuditLog.objects.filter(action=AUDIT_ACTION).order_by("created_at"))


def test_the_tool_is_registered_web_only():
    spec = get_read_tool(TOOL_NAME)
    assert spec is not None
    assert spec.channels == frozenset({CHANNEL_WEB})
    assert spec.source == "friday_sessions"


def test_owner_web_read_lists_the_workspace_sessions(owner, personal, hooks):
    _session(personal, hooks, "s-1", project="friday", notification="Claude needs permission")
    result = _read(personal, owner)
    assert '"project": "friday"' in result.text
    assert '"status": "running"' in result.text
    assert "Claude needs permission" in result.text
    # Session ids are bookkeeping, not answer material.
    assert "s-1" not in result.text


@pytest.mark.parametrize(
    "channel", [CHANNEL_CLI, CHANNEL_OPENAI, CHANNEL_TELEGRAM, CHANNEL_SYSTEM, CHANNEL_UNKNOWN]
)
def test_every_non_web_channel_is_refused(owner, personal, hooks, channel):
    _session(personal, hooks, "s-1")
    with pytest.raises(ReadToolError) as exc:
        _read(personal, owner, channel=channel)
    assert exc.value.code == "channel_forbidden"


def test_a_non_owner_member_is_refused(owner, firm, hooks):
    _session(firm, hooks, "firm-1", project="acme")
    member = register_user(email="member@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.ADMIN)
    with pytest.raises(ReadToolError) as exc:
        _read(firm, member)
    assert exc.value.code == "forbidden"


def test_an_agent_is_refused(personal, hooks):
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    WorkspaceMember.objects.create(workspace=personal, user=agent, role=WorkspaceMember.Role.MEMBER)
    with pytest.raises(ReadToolError) as exc:
        _read(personal, agent)
    assert exc.value.code == "forbidden"


def test_an_agent_flag_is_refused_even_with_the_owner_flag(owner, personal, hooks):
    # Only one installation owner can exist, so the double flag is modelled
    # on an unsaved instance: `is_agent` alone refuses the read.
    owner.is_agent = True
    with pytest.raises(ReadToolError) as exc:
        _read(personal, owner)
    assert exc.value.code == "forbidden"


def test_an_owner_outside_the_workspace_is_refused(owner, hooks):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    stranger_ws = Workspace.objects.get(owner=stranger)
    with pytest.raises(ReadToolError) as exc:
        _read(stranger_ws, owner)
    assert exc.value.code == "forbidden"


def test_another_workspace_sessions_are_never_listed(owner, personal, firm, hooks):
    _session(personal, hooks, "personal-1", project="friday")
    _session(firm, hooks, "firm-1", project="acme-secret-project")
    personal_text = _read(personal, owner).text
    firm_text = _read(firm, owner).text
    assert "friday" in personal_text and "acme-secret-project" not in personal_text
    assert "acme-secret-project" in firm_text and '"project": "friday"' not in firm_text


def test_a_forgotten_source_hides_its_sessions(owner, personal, hooks):
    _session(personal, hooks, "s-1")
    forget_source(hooks, actor=owner, channel=CHANNEL_WEB)
    result = _read(personal, owner)
    assert result.text == "No coding sessions recorded in this workspace."


def test_stale_sessions_are_reported_stale(owner, personal, hooks):
    _session(personal, hooks, "s-1", last_event_at=timezone.now() - timedelta(hours=25))
    assert '"status": "stale"' in _read(personal, owner).text


@pytest.mark.parametrize("args", [{"limit": 0}, {"limit": 99}, {"other": 1}, ["x"]])
def test_invalid_arguments_are_refused(owner, personal, hooks, args):
    with pytest.raises(ReadToolError) as exc:
        _read(personal, owner, args=args)
    assert exc.value.code == "invalid_arguments"


def test_limit_bounds_the_listing(owner, personal, hooks):
    for i in range(5):
        _session(personal, hooks, f"s-{i}", project=f"p{i}")
    assert len(_read(personal, owner, args={"limit": 2}).text.splitlines()) == 2


def test_every_read_writes_one_audit_row_without_text(owner, personal, hooks):
    session = _session(personal, hooks, "s-1", notification="private words here")
    _read(personal, owner)
    with pytest.raises(ReadToolError):
        _read(personal, owner, channel=CHANNEL_CLI)
    rows = _audits()
    assert [r.metadata["outcome"] for r in rows] == ["ok", "channel_forbidden"]
    assert rows[0].metadata["tool"] == TOOL_NAME
    assert rows[0].metadata["count"] == 1
    assert rows[0].metadata["session_ids"] == [str(session.id)]
    assert rows[0].workspace_id == personal.id
    for row in rows:
        assert "private words" not in str(row.metadata)


# The fence itself (`assistant.context.render_connector_block`) is exercised
# in `assistant/tests/test_owner_read_tools.py` -- `tools` may not import
# `assistant`.


def test_offered_only_on_web_where_sessions_exist(owner, personal, firm, hooks):
    def names(workspace, channel):
        return {spec.name for spec in list_read_tools(workspace=workspace, channel=channel)}

    assert TOOL_NAME not in names(personal, CHANNEL_WEB)
    _session(personal, hooks, "s-1")
    assert TOOL_NAME in names(personal, CHANNEL_WEB)
    assert TOOL_NAME not in names(personal, CHANNEL_CLI)
    assert TOOL_NAME not in names(firm, CHANNEL_WEB)
    forget_source(hooks, actor=owner, channel=CHANNEL_WEB)
    assert TOOL_NAME not in names(personal, CHANNEL_WEB)
