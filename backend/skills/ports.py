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
`skills.ports`, branch 7).
"""

from django.conf import settings
from django.db import transaction

from skills import services
from skills.models import InstructionSkill, InstructionSkillVersion

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
