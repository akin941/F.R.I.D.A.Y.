"""The `skills.proposals` probe (Phase 12 D1/D14/D16): pending skill
versions waiting for the owner's decision on `/skills`, and the open
archive suggestions of the workspace's latest curator run.

Labels are Friday templates over owner-typed or owner-approved names only:
an owner upload's name is the owner's text; a curator or playbook proposal
is labelled with the name of the already-approved skill it changes, or the
Friday words "yeni beceri" for a new one (a model-chosen name never
reaches a notice). An archive suggestion is labelled with the approved
skill's name. Keys are version ids and `archive:<run id>:<skill id>`. The
probe decides nothing.
"""

from django.conf import settings

from core import probes
from skills.models import InstructionSkill, InstructionSkillVersion, SkillCuratorRun

PROBE_NAME = "skills.proposals"


def _label(version):
    skill = version.skill
    if (
        version.proposed_by == InstructionSkillVersion.ProposedBy.OWNER
        or skill.current_version_id is not None
    ):
        return probes.safe_label(skill.name, "beceri", prefix="Beceri: ")
    return "Beceri: yeni beceri"


def open_archive_suggestions(actor, workspace):
    """[(run, suggestion, skill)] of the latest finished curator run whose
    skill is still active or stale and the suggestion not dismissed."""
    run = (
        SkillCuratorRun.objects.for_actor(actor, workspace=workspace)
        .exclude(outcome=SkillCuratorRun.Outcome.RUNNING)
        .order_by("-started_at")
        .first()
    )
    if run is None:
        return []
    wanted = run.open_suggestions()
    if not wanted:
        return []
    skills = {
        str(skill.pk): skill
        for skill in InstructionSkill.objects.for_actor(actor, workspace=workspace).filter(
            pk__in=[s["skill_id"] for s in wanted],
            status__in=[InstructionSkill.Status.ACTIVE, InstructionSkill.Status.STALE],
        )
    }
    return [(run, s, skills[s["skill_id"]]) for s in wanted if s["skill_id"] in skills]


def collect(workspace, owner, now) -> probes.ProbeResult:
    pending = (
        InstructionSkillVersion.objects.for_actor(owner, workspace=workspace)
        .filter(decision=InstructionSkillVersion.Decision.PENDING)
        .select_related("skill")
        .order_by("-created_at", "-id")
    )
    max_keys = int(settings.PROACTIVE_PROBE_MAX_KEYS)
    rows = list(pending[:max_keys])
    entries = [(str(row.pk), _label(row), f"/skills/{row.skill_id}") for row in rows]
    suggestions = open_archive_suggestions(owner, workspace)
    for run, _suggestion, skill in suggestions:
        entries.append(
            (
                f"archive:{run.pk}:{skill.pk}",
                probes.safe_label(skill.name, "beceri", prefix="Arşiv önerisi: "),
                f"/skills/{skill.pk}",
            )
        )
    entries = entries[:max_keys]
    items = tuple(
        probes.ProbeItem(key=key, label=label, link=link)
        for key, label, link in entries[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=pending.count() + len(suggestions),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(key for key, _, _ in entries),
    )


SPEC = probes.ProbeSpec(
    name=PROBE_NAME,
    owner_app="skills",
    collect=collect,
    digest_section="skills",
    notice_kind=PROBE_NAME,
    link="/skills",
    notice_template="{label} kararını bekliyor",
)


def register():
    probes.register_probe(SPEC)
