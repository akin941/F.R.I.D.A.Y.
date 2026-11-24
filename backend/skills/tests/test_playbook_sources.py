# ruff: noqa: F811 -- pytest fixtures imported from the helper module are redefined as arguments
"""Phase 12 D17: `skills.ports.record_playbook_source` -- what a source keeps
(paths, never contents; bounded context), what it refuses (sensitive repos,
failed tests, empty goal or test command), idempotency, the `.claude/skills/`
filter and workspace scoping."""

import uuid

import pytest

from skills import ports
from skills.models import PlaybookSource
from accounts.services import register_user
from skills.tests.helpers import PASSWORD, make_firm
from skills.tests.playbook_helpers import owner, record_source, workspace  # noqa: F401

pytestmark = pytest.mark.django_db


def test_a_source_is_recorded_with_paths_and_counts_only(workspace):
    source_id = record_source(workspace)
    source = PlaybookSource.objects.get(pk=source_id)
    assert source.workspace == workspace
    assert source.goal.startswith("acme-api: migration ekle")
    assert source.test_command == "make test"
    assert source.changed_paths == ["backend/app/migrations/0002_x.py", "backend/app/models.py"]
    assert source.test_summary == {"passed": True, "exit_code": 0, "changed_files": 2}
    assert source.distill_run is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"sensitive": True},
        {"test_passed": False},
        {"test_passed": None},
        {"goal": "   "},
        {"test_command": ""},
        {"job_id": "not-a-uuid"},
    ],
)
def test_refusals_record_nothing(workspace, overrides):
    assert record_source(workspace, **overrides) is None
    assert PlaybookSource.objects.count() == 0


def test_idempotent_per_job(workspace):
    job_id = uuid.uuid4()
    first = record_source(workspace, job_id=job_id)
    second = record_source(workspace, job_id=job_id, goal="başka bir hedef metni")
    assert first == second
    assert PlaybookSource.objects.count() == 1


def test_skill_paths_never_feed_a_source(workspace):
    source_id = record_source(
        workspace,
        changed_paths=[
            ".claude/skills/pr-yaz/SKILL.md",
            "sub/.claude/skills/x/references/a.md",
            ".claude/skills",
            "src/app.py",
            ".claude/settings.json",
            7,
        ],
    )
    assert PlaybookSource.objects.get(pk=source_id).changed_paths == [
        "src/app.py",
        ".claude/settings.json",
    ]


def test_context_text_is_bounded_and_invisible_formatting_stripped(workspace):
    source_id = record_source(workspace, context_text="a​" * 3000, goal="hedef‮ metni")
    source = PlaybookSource.objects.get(pk=source_id)
    assert len(source.context_text) == ports.CONTEXT_TEXT_MAX_CHARS
    assert "​" not in source.context_text
    assert source.goal == "hedef metni"


def test_sources_are_workspace_scoped(owner, workspace):
    member = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    firm = make_firm(owner, member)
    job_id = uuid.uuid4()
    record_source(workspace, job_id=job_id)
    record_source(firm, job_id=job_id)
    assert PlaybookSource.objects.filter(job_id=job_id).count() == 2
    assert PlaybookSource.objects.for_actor(member).filter(workspace=workspace).count() == 0
    assert PlaybookSource.objects.for_actor(member, workspace=firm).count() == 1
