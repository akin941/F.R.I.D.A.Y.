"""Retrieval modes under evaluation (brief D8, D9).

A retriever takes the chat turn's own arguments and returns what the turn
would put in the prompt: the ranked rows (for recall@10) and the context
text (for the leak check and the answer call). It goes through the same
selectors as the chat turn, so the workspace filter (`for_actor()`) and the
eligibility rules (`cloud_eligible()`, `model_eligible()`) apply exactly.

- `legacy` (today's default, `MEMORY_RETRIEVAL_MODE=legacy`):
  `memory.context.select_context_memories` and `render_memory_block`.
  Today's chat turn injects no facts and no episodes, so neither does this.
- `hybrid` is registered by branch 4 (`register("hybrid", ...)`), which also
  adds the `<memory_fact>` block.
"""

from dataclasses import dataclass, field

from memory.context import render_memory_block, select_context_memories

BASELINE_MODE = "legacy"


@dataclass
class Retrieved:
    # Row ids (memories, facts, episodes) in rank order.
    ranked_ids: list = field(default_factory=list)
    context_text: str = ""
    # Episodes that reached the context (for `needs_personal_data`).
    episodes: list = field(default_factory=list)


def legacy(*, actor, workspace, query_text, personal_data):
    memories = select_context_memories(
        actor=actor, workspace=workspace, query_text=query_text, personal_data=personal_data
    )
    return Retrieved(
        ranked_ids=[m.id for m in memories],
        context_text=render_memory_block(memories),
    )


RETRIEVERS = {BASELINE_MODE: legacy}


def register(mode, retriever):
    """Add a retrieval mode (branch 4: `hybrid`)."""
    RETRIEVERS[mode] = retriever


def retriever_for(mode):
    try:
        return RETRIEVERS[mode]
    except KeyError:
        raise ValueError(
            f"unknown retrieval mode {mode!r} (available: {', '.join(sorted(RETRIEVERS))})"
        ) from None
