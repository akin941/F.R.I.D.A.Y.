"""Reads of instruction skills (Phase 12 D13/D15), always through
`for_actor()` (ADR-001): a skill of one workspace is never offered,
matched, shown or routed in another."""

from skills.models import InstructionSkill, InstructionSkillVersion

Status = InstructionSkill.Status

# Offered to the matcher: active and stale (stale is metadata, D16).
USABLE_STATUSES = (Status.ACTIVE, Status.STALE)


def skills_for(actor, workspace):
    return InstructionSkill.objects.for_actor(actor, workspace=workspace).select_related(
        "current_version"
    )


def get_skill(actor, skill_id):
    return (
        InstructionSkill.objects.for_actor(actor)
        .select_related("current_version", "workspace")
        .filter(pk=skill_id)
        .first()
    )


def get_version(actor, version_id):
    return (
        InstructionSkillVersion.objects.for_actor(actor)
        .select_related("skill", "workspace")
        .filter(pk=version_id)
        .first()
    )


def usable_skills(actor, workspace):
    """The skills a turn may use: active or stale, with an approved
    current version, in this workspace only."""
    return list(
        skills_for(actor, workspace)
        .filter(status__in=USABLE_STATUSES, current_version__isnull=False)
        .order_by("name")
    )


def usable_skill_by_name(actor, workspace, name):
    if not isinstance(name, str) or not name:
        return None
    return (
        skills_for(actor, workspace)
        .filter(status__in=USABLE_STATUSES, current_version__isnull=False, name=name)
        .first()
    )


def has_usable_skills(actor, workspace):
    return (
        skills_for(actor, workspace)
        .filter(status__in=USABLE_STATUSES, current_version__isnull=False)
        .exists()
    )
