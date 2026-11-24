"""Phase 12 D17: `skills.ports.get_attachable_versions` -- the one read
`harness` makes of skills. Approved current versions of active (or stale)
skills of the workspace, with content and hash; anything else raises
`SkillVersionNotAttachable` naming the first offending id, with no partial
result."""

import ast
import uuid
from pathlib import Path

import pytest

from accounts.services import register_user
from core.channels import CHANNEL_WEB
from skills import ports, services
from skills.models import InstructionSkill
from skills.skill_md import parse_skill_md
from skills.tests.helpers import (
    PASSWORD,
    active_skill,
    approve,
    make_firm,
    make_owner,
    make_zip,
    personal_of,
    propose,
    skill_text,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_owner()


@pytest.fixture
def workspace(owner):
    return personal_of(owner)


@pytest.fixture
def firm(owner):
    member = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    return make_firm(owner, member)


def test_active_versions_come_back_with_content_and_hash(owner, workspace):
    data = make_zip(
        [
            ("SKILL.md", skill_text(body="Bak: references/check.md").encode()),
            ("references/check.md", b"- make test\n"),
        ]
    )
    parsed = services.parse_upload(data, filename="s.zip")
    version = services.propose_from_owner(
        actor=owner, channel=CHANNEL_WEB, workspace=workspace, parsed=parsed, origin="upload"
    )
    approve(version, owner)
    second = active_skill(workspace, owner, skill_text(name="ikinci"))
    result = ports.get_attachable_versions(
        workspace=workspace,
        version_ids=[str(second.current_version_id), version.pk, str(version.pk)],
    )
    assert [item.skill_name for item in result] == ["ikinci", "pr-yaz"]
    item = result[1]
    assert item.id == str(version.pk)
    assert item.content_sha256 == version.content_sha256
    assert item.references == (("references/check.md", "- make test\n"),)
    reparsed = parse_skill_md(item.skill_md, references=dict(item.references))
    assert reparsed.content_sha256 == version.content_sha256


def test_stale_skills_are_attachable(owner, workspace):
    skill = active_skill(workspace, owner)
    InstructionSkill.objects.filter(pk=skill.pk).update(status=InstructionSkill.Status.STALE)
    assert len(ports.get_attachable_versions(workspace=workspace, version_ids=[skill.current_version_id])) == 1


def _assert_refused(workspace, ids, offending):
    with pytest.raises(ports.SkillVersionNotAttachable) as caught:
        ports.get_attachable_versions(workspace=workspace, version_ids=ids)
    assert caught.value.version_id == str(offending)


def test_pending_rejected_archived_and_superseded_are_refused(owner, workspace):
    good = active_skill(workspace, owner, skill_text(name="iyi"))
    pending = propose(workspace, owner, skill_text(name="bekleyen"))
    _assert_refused(workspace, [good.current_version_id, pending.pk], pending.pk)

    rejected = propose(workspace, owner, skill_text(name="red"))
    services.reject_version(actor=owner, channel=CHANNEL_WEB, version=rejected)
    _assert_refused(workspace, [rejected.pk], rejected.pk)

    archived = active_skill(workspace, owner, skill_text(name="arsiv"))
    services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=archived)
    _assert_refused(workspace, [good.current_version_id, archived.current_version_id], archived.current_version_id)

    old_version_id = good.current_version_id
    newer = propose(workspace, owner, skill_text(name="iyi", body="Yeni gövde metni."))
    approve(newer, owner)
    _assert_refused(workspace, [old_version_id], old_version_id)
    assert ports.get_attachable_versions(workspace=workspace, version_ids=[newer.pk])


def test_unknown_malformed_and_other_workspace_ids_are_refused(owner, workspace, firm):
    mine = active_skill(workspace, owner, skill_text(name="benim"))
    theirs = active_skill(firm, owner, skill_text(name="firma"))
    unknown = uuid.uuid4()
    _assert_refused(workspace, [mine.current_version_id, unknown], unknown)
    _assert_refused(workspace, ["nope"], "nope")
    _assert_refused(workspace, [mine.current_version_id, theirs.current_version_id], theirs.current_version_id)
    _assert_refused(firm, [mine.current_version_id], mine.current_version_id)
    assert ports.get_attachable_versions(workspace=firm, version_ids=[theirs.current_version_id])


def test_too_many_are_refused(owner, workspace, settings):
    settings.SKILLS_ATTACH_MAX = 1
    a = active_skill(workspace, owner, skill_text(name="aa"))
    b = active_skill(workspace, owner, skill_text(name="bb"))
    with pytest.raises(ports.SkillVersionNotAttachable) as caught:
        ports.get_attachable_versions(
            workspace=workspace, version_ids=[a.current_version_id, b.current_version_id]
        )
    assert caught.value.reason == "too_many"
    assert ports.get_attachable_versions(workspace=workspace, version_ids=[]) == []


def test_harness_imports_only_skills_ports_from_skills():
    harness_dir = Path(__file__).resolve().parents[2] / "harness"
    offenders = []
    for path in harness_dir.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "skills":
                    names = {alias.name for alias in node.names}
                    if names != {"ports"}:
                        offenders.append((path.name, node.module, names))
                elif node.module.startswith("skills.") and node.module != "skills.ports":
                    offenders.append((path.name, node.module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("skills") and alias.name != "skills.ports":
                        offenders.append((path.name, alias.name))
    assert offenders == []
