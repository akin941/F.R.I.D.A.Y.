"""The `skills.proposals` probe (Phase 12 D1/D14): pending skill versions
waiting for the owner's decision on `/skills`.

Labels are Friday templates over owner-typed or owner-approved names only:
an owner upload's name is the owner's text; a curator or playbook proposal
is labelled with the name of the already-approved skill it changes, or the
Friday words "yeni beceri" for a new one (a model-chosen name never
reaches a notice). Keys are version ids. The probe decides nothing.
"""

from django.conf import settings

from core import probes
from skills.models import InstructionSkillVersion

PROBE_NAME = "skills.proposals"


def _label(version):
    skill = version.skill
    if (
        version.proposed_by == InstructionSkillVersion.ProposedBy.OWNER
        or skill.current_version_id is not None
    ):
        return probes.safe_label(skill.name, "beceri", prefix="Beceri: ")
    return "Beceri: yeni beceri"


def collect(workspace, owner, now) -> probes.ProbeResult:
    pending = (
        InstructionSkillVersion.objects.for_actor(owner, workspace=workspace)
        .filter(decision=InstructionSkillVersion.Decision.PENDING)
        .select_related("skill")
        .order_by("-created_at", "-id")
    )
    rows = list(pending[: int(settings.PROACTIVE_PROBE_MAX_KEYS)])
    items = tuple(
        probes.ProbeItem(key=str(row.pk), label=_label(row), link=f"/skills/{row.skill_id}")
        for row in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=pending.count(),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(str(row.pk) for row in rows),
    )


SPEC = probes.ProbeSpec(
    name=PROBE_NAME,
    owner_app="skills",
    collect=collect,
    digest_section="skills",
    notice_kind=PROBE_NAME,
    link="/skills",
    notice_template="{label} onayını bekliyor",
)


def register():
    probes.register_probe(SPEC)
