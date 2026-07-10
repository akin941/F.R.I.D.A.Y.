"""Structured output contract for the `router.intent` gateway call (MVP 6 §1).

The classification prompt instructs the model to return exactly one JSON
object of this shape:

    {"intent": "chat", "tool_name": null}

`intent` is exactly one of: chat, memory_query, knowledge_query, task_draft,
tool_request. `tool_name` names the registered tool the message appears to
request and is populated ONLY when `intent` is `tool_request` — it must be
null for every other intent value (never a value the caller has to strip
back out itself).

This parser validates `tool_name`'s *shape* only (non-empty string) —
whether it names a tool that actually exists is deliberately NOT checked
here (Codex round-1 review, LOW: flagged as a strictness gap, accepted as a
scope decision, not a bug). Real tool-name resolution against the registry
(`tools.registry.list_tool_names()`) lands in branch 4 (MVP 6 §4), at
confirm time — this branch has no dispatch side effects yet (every intent
still falls through to plain chat), so an unresolvable `tool_name` sits
inert in `intent_payload` until then. Validating against the registry here
too would duplicate that resolution logic across two branches for no
present benefit.

Anything else — malformed JSON, wrong shape, unexpected keys, an
unrecognized intent, a `tool_name` present/absent in the wrong case — is a
parse failure. Per the brief, classification failure always degrades to
`chat`: callers must catch `IntentParseError` and treat it exactly like a
`chat` classification with no `tool_name`, never propagate it to the user or
block the underlying reply.
"""

import json

from assistant.models import Message

_ALLOWED_INTENTS = frozenset(Message.Intent.values)
_TOP_LEVEL_KEYS = {"intent", "tool_name"}


class IntentParseError(Exception):
    """The classifier's output did not conform to the schema. Callers must
    degrade to `chat` (see module docstring) — never raised to the user."""


def parse_intent(raw_text):
    """Strictly parse and validate a `router.intent` response.

    Returns a `{"intent": str, "tool_name": str | None}` dict. Raises
    `IntentParseError` on the first violation found.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise IntentParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise IntentParseError("top-level payload must be a JSON object")
    if set(payload.keys()) != _TOP_LEVEL_KEYS:
        raise IntentParseError(f"unexpected top-level keys: {sorted(payload.keys())}")

    intent = payload["intent"]
    if not isinstance(intent, str) or intent not in _ALLOWED_INTENTS:
        raise IntentParseError(f"unrecognized intent value: {intent!r}")

    tool_name = payload["tool_name"]
    if intent == Message.Intent.TOOL_REQUEST:
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise IntentParseError("tool_request intent requires a non-empty tool_name")
    elif tool_name is not None:
        raise IntentParseError(f"tool_name must be null for intent {intent!r}")

    return {"intent": intent, "tool_name": tool_name}
