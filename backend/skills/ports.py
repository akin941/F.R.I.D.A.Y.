"""The proposal entry point for the curator and the playbook distiller
(Phase 12 D14, D16, D17).

Machine proposals land `pending` exactly like an owner upload: nothing
here approves, activates, archives or uses a skill. A proposal must pass
the same SKILL.md parser and refusals (permission keys, unknown keys, YAML
features, size), and each proposer has its own cap of pending proposals
per workspace (`SKILLS_MAX_PENDING_CURATOR` = 5,
`SKILLS_MAX_PENDING_PLAYBOOK` = 3). These are the only skill writes an
unattended run (the curator, the distiller) may make (D2): this module
calls no `deny_if_unattended`, while every owner service in
`skills.services` does.

Other apps reach skills only through this module (`harness` ->
`skills.ports`, branch 7):

- `record_playbook_source` -- `harness` records one successful, reviewed,
  tested, owner-typed implementation job as playbook input (D17);
- `get_attachable_versions` -- the one read `harness` makes of skills: the
  approved current versions an implementation job may mount, all or none.
"""

import re
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction

from skills import services
from skills.models import InstructionSkill, InstructionSkillVersion, PlaybookSource
from skills.skill_md import render_skill_md, strip_invisible

ProposedBy = InstructionSkillVersion.ProposedBy

_PROPOSERS = {
    ProposedBy.CURATOR: (InstructionSkill.Origin.CURATOR, "SKILLS_MAX_PENDING_CURATOR"),
    ProposedBy.PLAYBOOK: (InstructionSkill.Origin.PLAYBOOK, "SKILLS_MAX_PENDING_PLAYBOOK"),
}


class SkillProposalRefused(Exception):
    """A machine proposal was dropped; `code` is a closed token (the
    parser's code, `limit`, `invalid_state`, `name_mismatch`, `proposer`)."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def propose_version(*, workspace, text, proposed_by, provenance=None):
    """Propose one SKILL.md text as a `pending` version (a new skill, or a
    new version of the workspace's skill with that name). Returns the
    version id. Raises `SkillProposalRefused`."""
    if proposed_by not in _PROPOSERS:
        raise SkillProposalRefused("proposer")
    origin, cap_setting = _PROPOSERS[proposed_by]
    try:
        parsed = services.parse_text(text)
    except services.SkillServiceError as exc:
        raise SkillProposalRefused(exc.reason or "invalid") from None
    with transaction.atomic():
        services.lock_workspace(workspace)
        pending = InstructionSkillVersion.objects.filter(
            workspace=workspace,
            decision=InstructionSkillVersion.Decision.PENDING,
            proposed_by=proposed_by,
        ).count()
        if pending >= int(getattr(settings, cap_setting)):
            raise SkillProposalRefused("limit")
        try:
            version = services.create_version(
                workspace=workspace,
                parsed=parsed,
                proposed_by=proposed_by,
                origin=origin,
                provenance=provenance,
            )
        except services.SkillServiceError as exc:
            raise SkillProposalRefused(exc.code.removeprefix("skill.")) from None
    return version.pk


# --- playbook sources (D17) ---------------------------------------------------

# A mounted skill lives at `~/.claude/skills/<name>/`; an agent copying it
# into the repo would produce paths under this prefix. They never feed a
# playbook (no feedback loop, D17 "Exclusion" (4)).
SKILL_PATH_PATTERN = re.compile(r"(^|/)\.claude/skills(/|$)")
CONTEXT_TEXT_MAX_CHARS = 1000
GOAL_MAX_CHARS = 4000
TEST_COMMAND_MAX_CHARS = 1000
MAX_PATHS = 200
PATH_MAX_CHARS = 300


def is_skill_path(path) -> bool:
    return isinstance(path, str) and bool(SKILL_PATH_PATTERN.search(path))


def strip_skill_paths(paths):
    """`paths` without any `.claude/skills/` path (and without non-strings)."""
    return [p for p in (paths or []) if isinstance(p, str) and not is_skill_path(p)]


def record_playbook_source(
    *,
    workspace,
    job_id,
    repo_key,
    repo_label,
    goal,
    test_command,
    changed_paths,
    test_passed,
    exit_code=0,
    context_text="",
    sensitive=False,
):
    """Record one implementation job as playbook input (D17). The caller
    (`harness`) records only a `succeeded` job whose review approved it,
    whose tests passed and whose goal the owner typed; this port refuses
    anything that still looks otherwise -- a sensitive repo, failed tests,
    an empty goal or test command -- and returns None. Idempotent per
    (workspace, job). Never stores file contents; changed paths under
    `.claude/skills/` are dropped. Returns the source id or None."""
    if sensitive or test_passed is not True:
        return None
    goal = strip_invisible(goal or "").strip()
    test_command = strip_invisible(test_command or "").strip()
    if not goal or not test_command:
        return None
    paths = [p[:PATH_MAX_CHARS] for p in strip_skill_paths(changed_paths)][:MAX_PATHS]
    try:
        job_uuid = uuid.UUID(str(job_id))
    except (TypeError, ValueError):
        return None
    existing = PlaybookSource.objects.filter(workspace=workspace, job_id=job_uuid).first()
    if existing is not None:
        return existing.pk
    try:
        with transaction.atomic():
            source = PlaybookSource.objects.create(
                workspace=workspace,
                job_id=job_uuid,
                repo_key=str(repo_key)[:64],
                repo_label=strip_invisible(str(repo_label or "")).strip()[:200] or "repo",
                goal=goal[:GOAL_MAX_CHARS],
                test_command=test_command[:TEST_COMMAND_MAX_CHARS],
                changed_paths=paths,
                test_summary={
                    "passed": True,
                    "exit_code": exit_code if isinstance(exit_code, int) else None,
                    "changed_files": len(paths),
                },
                context_text=strip_invisible(context_text or "").strip()[:CONTEXT_TEXT_MAX_CHARS],
            )
    except IntegrityError:
        existing = PlaybookSource.objects.filter(workspace=workspace, job_id=job_uuid).first()
        return existing.pk if existing is not None else None
    return source.pk


# --- attaching skills to implementation jobs (D17 "Reuse") ----------------------


class SkillVersionNotAttachable(Exception):
    """`version_id` (the first offending id, as given) is unknown, not the
    approved current version of an active skill, or of another workspace.
    No partial result is ever returned."""

    def __init__(self, version_id, reason="not_attachable"):
        self.version_id = str(version_id)
        self.reason = reason
        super().__init__(f"Skill version {self.version_id} cannot be attached ({reason}).")


@dataclass(frozen=True)
class AttachableSkillVersion:
    """What a harness job may mount: the version's identity, its SKILL.md
    text and its reference files (`(path, text)` pairs)."""

    id: str
    skill_name: str
    version: int
    content_sha256: str
    skill_md: str
    references: tuple = ()


ATTACHABLE_STATUSES = (InstructionSkill.Status.ACTIVE, InstructionSkill.Status.STALE)


def get_attachable_versions(*, workspace, version_ids):
    """The approved current versions of `workspace`'s active (or stale)
    skills named by `version_ids`, in the given order, de-duplicated.
    Raises `SkillVersionNotAttachable` naming the first id that is not
    attachable (unknown, malformed, pending/rejected/superseded, not the
    skill's current version, an archived/rejected skill, another
    workspace), or when more than `SKILLS_ATTACH_MAX` are named."""
    ordered = []
    for raw in version_ids or []:
        try:
            parsed = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            raise SkillVersionNotAttachable(raw, "unknown") from None
        if parsed not in ordered:
            ordered.append(parsed)
    if len(ordered) > int(settings.SKILLS_ATTACH_MAX):
        raise SkillVersionNotAttachable(ordered[int(settings.SKILLS_ATTACH_MAX)], "too_many")
    if not ordered:
        return []
    rows = {
        row.pk: row
        for row in InstructionSkillVersion.objects.filter(
            workspace=workspace, pk__in=ordered
        ).select_related("skill")
    }
    result = []
    for version_id in ordered:
        row = rows.get(version_id)
        if row is None:
            raise SkillVersionNotAttachable(version_id, "unknown")
        skill = row.skill
        if (
            row.decision != InstructionSkillVersion.Decision.APPROVED
            or skill.current_version_id != row.pk
            or skill.status not in ATTACHABLE_STATUSES
            or skill.workspace_id != workspace.pk
        ):
            raise SkillVersionNotAttachable(version_id, "not_active")
        result.append(
            AttachableSkillVersion(
                id=str(row.pk),
                skill_name=skill.name,
                version=int(row.version),
                content_sha256=row.content_sha256,
                skill_md=render_skill_md(row.front_matter, row.body),
                references=tuple(sorted((row.references or {}).items())),
            )
        )
    return result
