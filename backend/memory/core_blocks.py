"""Core memory blocks: labels, budgets and the injection selection (Phase 8A
D7).

`memory` owns the rows; `assistant.context` renders the selected blocks
into the system prompt. The selection rules (D7, following the
`select_overlay_memories` precedent):

- `self` (it describes Friday, not the owner) on every channel and in every
  workspace -- read from the installation owner's personal workspace, where
  the assembler keeps it;
- `current_focus` / `active_projects` of the conversation's OWN workspace,
  on every channel, like memories;
- the owner's personal blocks (`developer`, `preferences`) only over
  `CHANNEL_WEB`, only for the owner (`workspace.owner_id == actor.id`, a
  human), and only when the conversation's workspace has no member besides
  the owner -- in a firm workspace that means a single-member firm. Each
  such read leaves a `memory.core_block.read` audit row (labels, versions,
  target workspace; never content);
- a `private` block is never injected (same rule as `cloud_eligible()`).
"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model

from audit.services import record_audit
from core.channels import CHANNEL_WEB
from memory.models import CoreMemoryBlock, Memory
from workspaces.models import Workspace

Label = CoreMemoryBlock.Label

# Hard per-label char budgets (D7, R6): <= 5100 chars in total.
CORE_BLOCK_CHAR_BUDGETS = {
    Label.SELF: 1500,
    Label.DEVELOPER: 1200,
    Label.CURRENT_FOCUS: 600,
    Label.ACTIVE_PROJECTS: 800,
    Label.PREFERENCES: 1000,
}
CORE_BLOCKS_TOTAL_CHAR_BUDGET = sum(CORE_BLOCK_CHAR_BUDGETS.values())

# Injection order (D7): self first, then the owner, then the workspace.
CORE_BLOCK_ORDER = (
    Label.SELF,
    Label.DEVELOPER,
    Label.CURRENT_FOCUS,
    Label.ACTIVE_PROJECTS,
    Label.PREFERENCES,
)

# Blocks that belong to the owner as a person: they live in the owner's
# personal workspace only and are injected only under the D7 conditions.
PERSONAL_LABELS = frozenset({Label.DEVELOPER, Label.PREFERENCES})
# Blocks that describe one workspace's work: any workspace may hold them.
WORKSPACE_LABELS = frozenset({Label.CURRENT_FOCUS, Label.ACTIVE_PROJECTS})
# Labels the owner edits through the API (`self` is assembler-only).
OWNER_EDITABLE_LABELS = PERSONAL_LABELS | WORKSPACE_LABELS


def labels_for_workspace(workspace):
    """The labels a workspace may hold: all five in a personal workspace,
    only the workspace labels in a company one."""
    if workspace.type == Workspace.Type.PERSONAL:
        return set(CORE_BLOCK_ORDER)
    return set(WORKSPACE_LABELS)


def owner_personal_workspace():
    """The installation owner's personal workspace, or None (fresh install,
    or no owner bootstrapped yet). Single-owner system (ADR-008): exactly
    one `is_owner` human."""
    owner = get_user_model().objects.filter(is_owner=True, is_agent=False, is_active=True).first()
    if owner is None:
        return None
    return (
        Workspace.objects.filter(owner=owner, type=Workspace.Type.PERSONAL)
        .order_by("created_at", "pk")
        .first()
    )


@dataclass(frozen=True)
class SelectedBlock:
    label: str
    content: str
    version: int
    workspace_id: str


def _bounded(content, label):
    budget = CORE_BLOCK_CHAR_BUDGETS[label]
    if len(content) > budget:
        return content[: budget - 1] + "…"
    return content


def _personal_blocks_allowed(*, actor, workspace, channel):
    if channel != CHANNEL_WEB:
        return False
    if actor is None or getattr(actor, "is_agent", False):
        return False
    if workspace.owner_id != actor.id:
        return False
    return not workspace.members.exclude(user_id=actor.id).exists()


def _injectable(queryset):
    return queryset.filter(sensitivity=Memory.Sensitivity.NORMAL).exclude(content="")


def select_core_blocks(*, actor, workspace, channel):
    """Return the ordered core blocks to inject for one turn (D7 rules in
    the module docstring). `workspace` is the conversation's workspace,
    already scoped by the caller; `channel` is required (no default, the
    `build_context` precedent)."""
    by_label = {}

    self_workspace = owner_personal_workspace()
    if self_workspace is not None:
        block = _injectable(
            CoreMemoryBlock.objects.filter(workspace=self_workspace, label=Label.SELF)
        ).first()
        if block is not None:
            by_label[Label.SELF] = block

    own = _injectable(
        CoreMemoryBlock.objects.for_actor(actor, workspace=workspace).filter(
            label__in=WORKSPACE_LABELS
        )
    )
    for block in own:
        by_label[block.label] = block

    personal_read = []
    if _personal_blocks_allowed(actor=actor, workspace=workspace, channel=channel):
        personal_workspace = (
            Workspace.objects.filter(owner_id=actor.id, type=Workspace.Type.PERSONAL)
            .order_by("created_at", "pk")
            .first()
        )
        if personal_workspace is not None:
            personal = _injectable(
                CoreMemoryBlock.objects.filter(
                    workspace=personal_workspace, label__in=PERSONAL_LABELS
                )
            )
            for block in personal:
                by_label[block.label] = block
                personal_read.append(block)
            if personal_read:
                record_audit(
                    action="memory.core_block.read",
                    actor=actor,
                    workspace=personal_workspace,
                    metadata={
                        "labels": sorted(b.label for b in personal_read),
                        "versions": {b.label: b.version for b in personal_read},
                        "target_workspace_id": str(workspace.id),
                        "channel": channel,
                    },
                )

    selected = []
    for label in CORE_BLOCK_ORDER:
        block = by_label.get(label)
        if block is None:
            continue
        selected.append(
            SelectedBlock(
                label=label,
                content=_bounded(block.content, label),
                version=block.version,
                workspace_id=str(block.workspace_id),
            )
        )
    return selected
