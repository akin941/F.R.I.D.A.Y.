"""Deterministic `self` core block assembler (Phase 8A D8).

A fixed Turkish template filled ONLY from:

- the committed source snapshot (`assistant/self_sources.json`, built from
  CLAUDE.md, the accepted ADRs and the MASTER_PLAN phases table);
- the tool registries (`tools.registry.list_tool_names`,
  `list_read_tool_names`): names only;
- the active `ModelProvider` names (never keys, never `base_url`s).

It makes NO model call: Friday never invents facts about itself. The block
is written through `memory.services.set_assembled_self_block`, the only
writer of `label=self`, which versions it only when the content changes.
Triggered after `migrate` (a `post_migrate` handler, i.e. on deploy) and by
a daily beat (`assistant.tasks.assemble_self_block_task`).

Every output line is one of `TEMPLATES` with its slots filled from those
sources; a test checks exactly that. Lines are added in priority order and
a line that would exceed the 1500-char `self` budget is skipped
(deterministic, the firm-profile precedent).
"""

import logging

from ai_core.models import ModelProvider
from assistant.self_sources import load_snapshot
from memory.core_blocks import CORE_BLOCK_CHAR_BUDGETS, owner_personal_workspace
from memory.models import CoreMemoryBlock
from memory.services import set_assembled_self_block
from tools.registry import list_read_tool_names, list_tool_names

logger = logging.getLogger(__name__)

SELF_BUDGET = CORE_BLOCK_CHAR_BUDGETS[CoreMemoryBlock.Label.SELF]

# The whole vocabulary of the `self` block. `{...}` slots are filled from
# the snapshot or the registries; everything else is template text.
TEMPLATES = {
    "identity": 'Ben Friday\'im. Kimliğim (CLAUDE.md): "{identity}"',
    "phase": 'Şu anki faz: {phase}. Hedef: "{outcome}"',
    "developer": (
        "Beni geliştiren kişi hakkında bildiklerim yalnızca developer bloğundadır; "
        "orada olmayan bir şeyi uydurmam."
    ),
    "tools": "Kullanabildiğim araçlar: {tools}.",
    "read_tools": "Okuma araçlarım: {read_tools}.",
    "providers": "Etkin model sağlayıcılarım: {providers}.",
    "rules_header": "Uyduğum kurallar:",
    "rule": "- {rule}",
    "adrs_header": "Kabul edilmiş mimari kararlarım:",
    "adr": "- {adr_id} {adr_title}: {adr_decision}",
}

# Capability lines (tool and provider inventory). Injected only on the
# owner's interactive channels (web, Telegram); a cli/openai facade turn
# gets the block without them (brief follow-up "D7 `self` block on
# low-trust channels").
CAPABILITY_TEMPLATES = ("tools", "read_tools", "providers")
CAPABILITY_PREFIXES = tuple(TEMPLATES[name].split("{")[0] for name in CAPABILITY_TEMPLATES)


def _provider_names():
    return sorted(ModelProvider.objects.filter(is_active=True).values_list("name", flat=True))


def assemble_self_content(snapshot, *, tools, read_tools, providers):
    """Pure: the `self` block text for this snapshot and registry state."""
    candidates = []
    if snapshot:
        candidates.append(TEMPLATES["identity"].format(identity=snapshot["identity"]["text"]))
        phase = snapshot.get("phase")
        if phase:
            candidates.append(
                TEMPLATES["phase"].format(phase=phase["phase"], outcome=phase["outcome"])
            )
    candidates.append(TEMPLATES["developer"])
    if tools:
        candidates.append(TEMPLATES["tools"].format(tools=", ".join(tools)))
    if read_tools:
        candidates.append(TEMPLATES["read_tools"].format(read_tools=", ".join(read_tools)))
    if providers:
        candidates.append(TEMPLATES["providers"].format(providers=", ".join(providers)))
    if snapshot:
        rules = snapshot.get("rules") or []
        if rules:
            candidates.append(TEMPLATES["rules_header"])
            candidates.extend(TEMPLATES["rule"].format(rule=r["text"]) for r in rules)
        adrs = snapshot.get("adrs") or []
        if adrs:
            candidates.append(TEMPLATES["adrs_header"])
            candidates.extend(
                TEMPLATES["adr"].format(
                    adr_id=a["id"], adr_title=a["title"], adr_decision=a["decision"]["text"]
                )
                for a in adrs
            )

    lines = []
    used = 0
    for line in candidates:
        cost = len(line) + (1 if lines else 0)
        if used + cost > SELF_BUDGET:
            continue  # deterministic: skip what doesn't fit, keep scanning
        lines.append(line)
        used += cost
    return "\n".join(lines)


def build_self_content(snapshot=None):
    snapshot = snapshot if snapshot is not None else load_snapshot()
    return assemble_self_content(
        snapshot,
        tools=list_tool_names(),
        read_tools=list_read_tool_names(),
        providers=_provider_names(),
    )


def refresh_self_block(snapshot=None):
    """Assemble and store the `self` block. Returns `(block, changed)`, or
    `(None, False)` when there is no owner workspace yet (fresh install)."""
    workspace = owner_personal_workspace()
    if workspace is None:
        return None, False
    return set_assembled_self_block(workspace=workspace, content=build_self_content(snapshot))


def strip_capability_lines(content):
    """The `self` block without its tool/provider inventory lines."""
    return "\n".join(
        line for line in content.split("\n") if not line.startswith(CAPABILITY_PREFIXES)
    )


def refresh_self_block_safely(**_kwargs):
    """`post_migrate` handler: a deploy must never fail because `self`
    could not be assembled (the daily beat retries)."""
    try:
        refresh_self_block()
    except Exception:  # noqa: BLE001 - logged, retried by the beat
        logger.exception("self block assembly failed")
