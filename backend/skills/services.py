"""The staged approval lifecycle of instruction skills (Phase 12 D14).

Every decision -- approve, edit-then-approve, reject, archive, restore --
is the workspace owner's, over the web (`core.owner_web`), one item at a
time. There is **no bulk approve** and no "allow always": a version is
approved by its content hash, and a version whose hash differs from the
one the owner was shown is refused (`skill.version_changed`). No model
approves, ranks-to-approve or promotes anything; curator and playbook
proposals land `pending` like an upload (`skills.ports`).

Nothing is deleted: skills and versions are archived. Every transition
writes an audit row with ids and hashes only, never content
(`skills.version.proposed` / `.approved` / `.rejected` / `.superseded`,
`skills.skill.archived` / `.restored`). Inside an unattended run every
owner service here is refused (`skill_decision` / `skill_write`).
"""

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from audit.services import record_audit
from core.owner_web import OwnerWebRequired, require_owner_web
from core.unattended import deny_if_unattended
from skills.models import InstructionSkill, InstructionSkillVersion
from skills.skill_md import (
    ParsedSkill,
    SkillBundleError,
    SkillParseError,
    parse_skill_md,
    read_bundle,
)

Status = InstructionSkill.Status
Decision = InstructionSkillVersion.Decision
ProposedBy = InstructionSkillVersion.ProposedBy


class SkillServiceError(Exception):
    """A refused skill write; `code` is a closed token (`core.error_codes`).
    `reason` carries the parser's own closed code where there is one."""

    code = "skill.invalid"

    def __init__(self, message="", *, reason=""):
        self.reason = reason
        super().__init__(message or self.code)


class SkillInvalid(SkillServiceError):
    code = "skill.invalid"


class SkillBundleRefused(SkillServiceError):
    code = "skill.bundle_refused"


class SkillBundleTooLarge(SkillServiceError):
    code = "skill.bundle_too_large"


class SkillHostExecutionRefused(SkillServiceError):
    code = "skill.host_execution_refused"


class SkillVersionChanged(SkillServiceError):
    code = "skill.version_changed"


class SkillInvalidState(SkillServiceError):
    code = "skill.invalid_state"


class SkillLimit(SkillServiceError):
    code = "skill.limit"


class SkillNameMismatch(SkillServiceError):
    code = "skill.name_mismatch"


class SkillPermissionDenied(Exception):
    """Not the owner over the web (`owner_required` / `channel_forbidden`,
    the generic `core.owner_web` codes)."""

    def __init__(self, message, code="owner_required"):
        self.code = code
        super().__init__(message)


_BUNDLE_ERRORS = {
    "bundle_refused": SkillBundleRefused,
    "bundle_too_large": SkillBundleTooLarge,
    "host_execution_refused": SkillHostExecutionRefused,
}


def _require_owner(*, actor, workspace, channel):
    try:
        require_owner_web(actor=actor, workspace=workspace, channel=channel)
    except OwnerWebRequired as exc:
        raise SkillPermissionDenied(str(exc), code=exc.code) from None


def _audit(action, *, actor, workspace, resource, **metadata):
    record_audit(
        action=action,
        actor=actor,
        actor_type="human" if actor is not None else "system",
        workspace=workspace,
        resource=resource,
        metadata=metadata,
    )


def _version_meta(version):
    return {
        "skill_id": str(version.skill_id),
        "version_id": str(version.pk),
        "version": version.version,
        "sha256": version.content_sha256,
    }


def parse_text(text) -> ParsedSkill:
    try:
        return parse_skill_md(text)
    except SkillParseError as exc:
        raise SkillInvalid(str(exc), reason=exc.code) from None


def parse_upload(data, *, filename) -> ParsedSkill:
    try:
        return read_bundle(data, filename=filename)
    except SkillBundleError as exc:
        raise _BUNDLE_ERRORS.get(exc.code, SkillBundleRefused)(str(exc), reason=exc.code) from None
    except SkillParseError as exc:
        raise SkillInvalid(str(exc), reason=exc.code) from None


def lock_workspace(workspace):
    # Serialize skill writes per workspace so caps and version numbers see
    # every committed row.
    type(workspace).objects.select_for_update().filter(pk=workspace.pk).first()


def active_count(workspace):
    return InstructionSkill.objects.filter(
        workspace=workspace, status__in=[Status.ACTIVE, Status.STALE]
    ).count()


def create_version(
    *,
    workspace,
    parsed: ParsedSkill,
    proposed_by,
    origin,
    actor=None,
    provenance=None,
    skill=None,
):
    """Insert one `pending` version (the shared write behind the owner
    services and `skills.ports`). Caller holds the workspace lock. An
    earlier pending version of the same skill is `superseded` (one pending
    proposal per skill); the same content as the pending one is returned
    as is."""
    if skill is None:
        skill = InstructionSkill.objects.filter(workspace=workspace, name=parsed.name).first()
    elif skill.name != parsed.name:
        raise SkillNameMismatch("A skill's name cannot change; propose a new skill.")
    if skill is not None and skill.status == Status.ARCHIVED:
        raise SkillInvalidState("Restore the archived skill before proposing a new version.")
    created_skill = skill is None
    if created_skill:
        skill = InstructionSkill.objects.create(
            workspace=workspace,
            name=parsed.name,
            status=Status.PROPOSED,
            origin=origin,
            created_by=actor,
        )
    sha = parsed.content_sha256
    pending = list(
        InstructionSkillVersion.objects.filter(skill=skill, decision=Decision.PENDING).order_by(
            "version"
        )
    )
    for previous in pending:
        if previous.content_sha256 == sha and previous.proposed_by == proposed_by:
            return previous
    current = skill.current_version
    if current is not None and current.content_sha256 == sha:
        raise SkillInvalidState("This exact version is already in use.")
    last = (
        InstructionSkillVersion.objects.filter(skill=skill)
        .order_by("-version")
        .values_list("version", flat=True)
        .first()
    )
    version = InstructionSkillVersion.objects.create(
        workspace=workspace,
        skill=skill,
        version=(last or 0) + 1,
        front_matter=parsed.front_matter,
        body=parsed.body,
        references=dict(parsed.references),
        content_sha256=sha,
        proposed_by=proposed_by,
        provenance=dict(provenance or {}),
        created_by=actor,
    )
    now = timezone.now()
    for previous in pending:
        previous.decision = Decision.SUPERSEDED
        previous.decided_at = now
        previous.save(update_fields=["decision", "decided_at", "updated_at"])
        _audit(
            "skills.version.superseded",
            actor=actor,
            workspace=workspace,
            resource=previous,
            **_version_meta(previous),
            by_version_id=str(version.pk),
        )
    if skill.status == Status.REJECTED:
        skill.status = Status.PROPOSED
        skill.save(update_fields=["status", "updated_at"])
    _audit(
        "skills.version.proposed",
        actor=actor,
        workspace=workspace,
        resource=version,
        **_version_meta(version),
        proposed_by=proposed_by,
        new_skill=created_skill,
    )
    return version


def propose_from_owner(*, actor, channel, workspace, parsed: ParsedSkill, origin, skill=None):
    """The owner proposes a version (an upload, or text typed in the
    editor). It stays `pending` until the owner approves that exact hash."""
    deny_if_unattended("skill_write")
    _require_owner(actor=actor, workspace=workspace, channel=channel)
    with transaction.atomic():
        lock_workspace(workspace)
        return create_version(
            workspace=workspace,
            parsed=parsed,
            proposed_by=ProposedBy.OWNER,
            origin=origin,
            actor=actor,
            skill=skill,
        )


def _locked_version(version):
    return (
        InstructionSkillVersion.objects.select_for_update()
        .select_related("skill", "workspace")
        .get(pk=version.pk)
    )


def _approve_locked(*, actor, version, now):
    skill = InstructionSkill.objects.select_for_update().get(pk=version.skill_id)
    if skill.status == Status.ARCHIVED:
        raise SkillInvalidState("Restore the archived skill first.")
    if skill.status not in (Status.ACTIVE, Status.STALE) and active_count(version.workspace) >= int(
        settings.SKILLS_MAX_ACTIVE
    ):
        raise SkillLimit(f"At most {settings.SKILLS_MAX_ACTIVE} active skills per workspace.")
    version.decision = Decision.APPROVED
    version.decided_by = actor
    version.decided_at = now
    version.save(update_fields=["decision", "decided_by", "decided_at", "updated_at"])
    skill.current_version = version
    skill.status = Status.ACTIVE
    skill.save(update_fields=["current_version", "status", "updated_at"])
    version.skill = skill
    _audit(
        "skills.version.approved",
        actor=actor,
        workspace=version.workspace,
        resource=version,
        **_version_meta(version),
    )
    return version


def _check_pending(version, expected_sha256):
    if version.decision != Decision.PENDING:
        raise SkillInvalidState("This version was already decided.")
    if not expected_sha256 or version.content_sha256 != expected_sha256:
        raise SkillVersionChanged("This version changed since it was shown; reload it.")


def approve_version(*, actor, channel, version, expected_sha256):
    """The owner approves one pending version, by the hash they were
    shown. The skill becomes `active` with this version current."""
    deny_if_unattended("skill_decision")
    _require_owner(actor=actor, workspace=version.workspace, channel=channel)
    with transaction.atomic():
        lock_workspace(version.workspace)
        locked = _locked_version(version)
        _check_pending(locked, expected_sha256)
        return _approve_locked(actor=actor, version=locked, now=timezone.now())


def edit_and_approve(*, actor, channel, version, expected_sha256, text):
    """The owner edits a pending version and approves the edit: a new
    version (proposed by the owner) is inserted and approved; the edited
    one is `superseded`. The name cannot change."""
    deny_if_unattended("skill_decision")
    _require_owner(actor=actor, workspace=version.workspace, channel=channel)
    parsed = parse_text(text)
    with transaction.atomic():
        lock_workspace(version.workspace)
        locked = _locked_version(version)
        _check_pending(locked, expected_sha256)
        if parsed.name != locked.skill.name:
            raise SkillNameMismatch("A skill's name cannot change; propose a new skill.")
        if parsed.content_sha256 == locked.content_sha256:
            return _approve_locked(actor=actor, version=locked, now=timezone.now())
        new_version = create_version(
            workspace=locked.workspace,
            parsed=parsed,
            proposed_by=ProposedBy.OWNER,
            origin=locked.skill.origin,
            actor=actor,
            skill=locked.skill,
            provenance={"edited_from": str(locked.pk), "edited_from_sha256": locked.content_sha256},
        )
        return _approve_locked(actor=actor, version=new_version, now=timezone.now())


def reject_version(*, actor, channel, version, expected_sha256=None):
    """The owner rejects one pending version. A skill with no approved
    version becomes `rejected`; an active skill keeps its current one."""
    deny_if_unattended("skill_decision")
    _require_owner(actor=actor, workspace=version.workspace, channel=channel)
    with transaction.atomic():
        lock_workspace(version.workspace)
        locked = _locked_version(version)
        if locked.decision != Decision.PENDING:
            raise SkillInvalidState("This version was already decided.")
        if expected_sha256 and expected_sha256 != locked.content_sha256:
            raise SkillVersionChanged("This version changed since it was shown; reload it.")
        locked.decision = Decision.REJECTED
        locked.decided_by = actor
        locked.decided_at = timezone.now()
        locked.save(update_fields=["decision", "decided_by", "decided_at", "updated_at"])
        skill = InstructionSkill.objects.select_for_update().get(pk=locked.skill_id)
        if skill.current_version_id is None and skill.status == Status.PROPOSED:
            skill.status = Status.REJECTED
            skill.save(update_fields=["status", "updated_at"])
        _audit(
            "skills.version.rejected",
            actor=actor,
            workspace=locked.workspace,
            resource=locked,
            **_version_meta(locked),
        )
        return locked


def archive_skill(*, actor, channel, skill):
    """The owner archives an active or stale skill (one click, reversible
    with `restore_skill`). It is no longer offered to any turn."""
    deny_if_unattended("skill_decision")
    _require_owner(actor=actor, workspace=skill.workspace, channel=channel)
    with transaction.atomic():
        lock_workspace(skill.workspace)
        locked = InstructionSkill.objects.select_for_update().get(pk=skill.pk)
        if locked.status not in (Status.ACTIVE, Status.STALE):
            raise SkillInvalidState("Only an active or stale skill can be archived.")
        previous = locked.status
        locked.status = Status.ARCHIVED
        locked.save(update_fields=["status", "updated_at"])
        _audit(
            "skills.skill.archived",
            actor=actor,
            workspace=locked.workspace,
            resource=locked,
            skill_id=str(locked.pk),
            previous=previous,
            version_id=str(locked.current_version_id or ""),
        )
        return locked


def restore_skill(*, actor, channel, skill):
    """The owner restores an archived skill to `active` (its approved
    current version), within the active cap."""
    deny_if_unattended("skill_decision")
    _require_owner(actor=actor, workspace=skill.workspace, channel=channel)
    with transaction.atomic():
        lock_workspace(skill.workspace)
        locked = InstructionSkill.objects.select_for_update().get(pk=skill.pk)
        if locked.status != Status.ARCHIVED or locked.current_version_id is None:
            raise SkillInvalidState("Only an archived skill with an approved version.")
        if active_count(locked.workspace) >= int(settings.SKILLS_MAX_ACTIVE):
            raise SkillLimit(f"At most {settings.SKILLS_MAX_ACTIVE} active skills per workspace.")
        locked.status = Status.ACTIVE
        locked.save(update_fields=["status", "updated_at"])
        _audit(
            "skills.skill.restored",
            actor=actor,
            workspace=locked.workspace,
            resource=locked,
            skill_id=str(locked.pk),
            version_id=str(locked.current_version_id),
        )
        return locked


def record_use(skill_id, *, now=None):
    """Usage telemetry (the curator's input, D15/D16): one counter bump
    and the last-used time. The only write a turn's skill use makes."""
    InstructionSkill.objects.filter(pk=skill_id).update(
        use_count=F("use_count") + 1, last_used_at=now or timezone.now()
    )
