"""Context builder v1 — assembles the message list sent to the gateway.

v1 adds curated memory injection (MVP 2 §2): approved, normal-sensitivity
memories of exactly `conversation.workspace`, selected and framed by
`memory.context` (the single sanctioned Memory→gateway path). MVP 3 branch 4
adds RAG knowledge injection as a separate, parallel step (`knowledge.context`
— vector similarity, not curated approval): both are independent selection
mechanisms and neither is merged into the other's call. No summarization or
truncation of history yet. The active `jarvis_system` prompt is mandatory: a
missing template or active version is a server misconfiguration, not
something to paper over with a fallback prompt.

MVP 6 §2 adds intent-aware framing for `memory_query`/`knowledge_query`
turns: neither retrieval mechanism above changes (memory has no embeddings
and this MVP does not add them; knowledge's own semantic search already runs
every turn) — only the *budget* (`knowledge_query` widens
`select_context_chunks`'s window for this turn only) and an explicit
system-prompt instruction telling the model what the user is specifically
asking for. Both are additive parameters to this function, not new
selection algorithms.
"""

from django.core.exceptions import ImproperlyConfigured

from ai_core.models import PromptTemplate
from assistant.models import Message
from knowledge.context import render_knowledge_block, select_context_chunks
from memory.context import render_memory_block, select_context_memories

# Widened window for a `knowledge_query` turn only (MVP 6 §2) — same
# selection/ranking logic as the default, just a bigger budget; see
# `knowledge.context.select_context_chunks`'s own module constants for the
# unwidened defaults every other intent still uses.
KNOWLEDGE_QUERY_MAX_CHUNKS = 20
KNOWLEDGE_QUERY_CHAR_BUDGET = 8000

# Explicit per-intent framing appended to the system prompt (MVP 6 §2) —
# tells the model what this specific turn is about, on top of the
# already-injected memory/knowledge blocks below. Neither string changes
# what gets retrieved; only how the model is told to use it.
KNOWLEDGE_QUERY_INSTRUCTION = (
    "\n\nThe user is specifically asking about their knowledge base this "
    "turn. Answer using the retrieved knowledge below when it's relevant; if "
    "nothing retrieved actually answers the question, say so plainly rather "
    "than guessing."
)
MEMORY_QUERY_INSTRUCTION = (
    "\n\nThe user is specifically asking about their own remembered/curated "
    "information this turn. Answer using the curated memories below when "
    "they're relevant; if nothing retrieved actually answers the question, "
    "say so plainly rather than guessing."
)


def build_context(conversation, *, actor, new_user_content, intent=Message.Intent.CHAT):
    """Build (messages, context_report, prompt_version, memory_ids,
    chunk_ids) for a gateway call.

    `conversation` must already be scoped via `for_actor()` by the caller. The
    caller must reuse the returned `prompt_version` for the gateway call
    itself, rather than re-querying `active_version` — prompts are versioned
    but `active_version` can be repointed at any time, and re-querying could
    make the gateway call (and its recorded ModelUsage) reference a different
    version than the one whose content actually went into `messages`.

    `memory_ids`/`chunk_ids` are the ordered lists of injected memory/
    knowledge-chunk UUIDs (strings); the caller persists them on the
    assistant Message row (`memories_used`/`chunks_used`) so the frontend
    indicators survive reload without ever exposing `context_report`.

    `intent` is the router's classification of the user's message (MVP 6
    §1/§2, default `chat` for pre-MVP-6 callers/rows). It only affects
    knowledge-retrieval budget and system-prompt framing — never which rows
    are selected in the first place.
    """
    try:
        template = PromptTemplate.objects.get(name="jarvis_system")
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured("jarvis_system prompt template is not configured") from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured("jarvis_system prompt template has no active version")
    prompt_version = template.active_version

    memories = select_context_memories(actor=actor, workspace=conversation.workspace)
    memory_block = render_memory_block(memories)
    memory_ids = [str(m.id) for m in memories]

    if intent == Message.Intent.KNOWLEDGE_QUERY:
        chunks = select_context_chunks(
            actor=actor,
            workspace=conversation.workspace,
            query_text=new_user_content,
            max_chunks=KNOWLEDGE_QUERY_MAX_CHUNKS,
            char_budget=KNOWLEDGE_QUERY_CHAR_BUDGET,
        )
    else:
        chunks = select_context_chunks(
            actor=actor, workspace=conversation.workspace, query_text=new_user_content
        )
    knowledge_block = render_knowledge_block(chunks)
    chunk_ids = [str(c.id) for c in chunks]

    if intent == Message.Intent.KNOWLEDGE_QUERY:
        intent_instruction = KNOWLEDGE_QUERY_INSTRUCTION
    elif intent == Message.Intent.MEMORY_QUERY:
        intent_instruction = MEMORY_QUERY_INSTRUCTION
    else:
        intent_instruction = ""

    messages = [
        {
            "role": "system",
            "content": prompt_version.content + intent_instruction + memory_block + knowledge_block,
        }
    ]

    # workspace= is redundant in the normal write path (application code always
    # copies it from conversation.workspace) but guards ADR-001's workspace
    # invariant against a stray admin edit or future bug putting a Message
    # under the wrong workspace.
    history = conversation.messages.filter(
        status=Message.Status.COMPLETE, workspace=conversation.workspace
    ).order_by("created_at", "id")
    history_message_count = 0
    for message in history:
        messages.append({"role": message.role, "content": message.content})
        history_message_count += 1

    messages.append({"role": "user", "content": new_user_content})

    context_report = {
        "context_builder": "v1",
        "history_message_count": history_message_count,
        "total_message_count": len(messages),
        "total_characters": sum(len(m["content"]) for m in messages),
        "truncated": False,
        "memory_count": len(memory_ids),
        "memory_ids": memory_ids,
        "chunk_count": len(chunk_ids),
        "chunk_ids": chunk_ids,
        "intent": intent,
    }
    return messages, context_report, prompt_version, memory_ids, chunk_ids
