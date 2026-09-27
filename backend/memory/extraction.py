"""Structured output contract for the `memory.extraction` gateway call
(MVP 2 §3).

The extraction prompt instructs the model to return exactly one JSON object
of this shape:

    {"version": 1, "candidates": [{"type": "semantic", "title": "...", "content": "..."}]}

Anything else — malformed JSON, wrong shape, unexpected keys, an invalid
`type`, an oversized/empty field, too many candidates — is a parse failure.
Per the brief, a schema-validation failure is always a **terminal,
non-retryable** outcome: never a crash, never a partial write, and never
assumed to be a transient provider hiccup the model might self-correct on
identical input. "Strict" is taken literally: exact top-level and
per-candidate key sets, no tolerance for extra fields, and `bool` is
explicitly rejected as a version/type value (Python's `True == 1` would
otherwise silently pass a boolean off as the integer schema version).
"""

import json

from memory.models import Memory

SCHEMA_VERSION = 1
MAX_CANDIDATES_PER_JOB = 10
MAX_TITLE_CHARS = 255  # matches Memory.title's max_length
MAX_CONTENT_CHARS = 4000

# WORK_LOG excluded (Codex round-6 P2 on feat/p1-work-log): it is reserved
# for `record_work_log()`'s `system_generated` pairing (ADR-008 k.4) —
# conversation extraction must never let the model tag a candidate as
# work_log, or finalize_extraction_success() would create an ai_suggested
# work-log row, bypassing the single-choke-point guarantee the moment a
# curator approves it without touching memory_type.
_ALLOWED_TYPES = frozenset(Memory.Type.values) - {Memory.Type.WORK_LOG}
_TOP_LEVEL_KEYS = {"version", "candidates"}
_CANDIDATE_KEYS = {"type", "title", "content"}


class ExtractionParseError(Exception):
    """The extractor's output did not conform to the schema. Always terminal
    (see module docstring) — callers must mark the job `failed`, never
    retry it."""


def parse_candidates(raw_text):
    """Strictly parse and validate a `memory.extraction` response.

    Returns a list of `{"type", "title", "content"}` dicts in the model's
    original order. Raises `ExtractionParseError` on the first violation
    found — no partial results.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ExtractionParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ExtractionParseError("top-level payload must be a JSON object")
    if set(payload.keys()) != _TOP_LEVEL_KEYS:
        raise ExtractionParseError(f"unexpected top-level keys: {sorted(payload.keys())}")

    version = payload["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise ExtractionParseError(f"unsupported schema version: {version!r}")

    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise ExtractionParseError("'candidates' must be a list")
    if len(candidates) > MAX_CANDIDATES_PER_JOB:
        raise ExtractionParseError(
            f"too many candidates: {len(candidates)} > {MAX_CANDIDATES_PER_JOB}"
        )

    parsed = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ExtractionParseError(f"candidate {index} is not an object")
        if set(candidate.keys()) != _CANDIDATE_KEYS:
            raise ExtractionParseError(
                f"candidate {index} has unexpected keys: {sorted(candidate.keys())}"
            )

        # Type-check BEFORE any membership/comparison test below — a
        # non-hashable value (list, dict) would otherwise raise a raw
        # TypeError from `in _ALLOWED_TYPES`, escaping the terminal
        # ExtractionParseError contract this module exists to guarantee.
        # (`bool` needs no special case here, unlike for `version`: bool is
        # never an instance of `str`, so it's already rejected below.)
        # Neither message below embeds the raw `type` value: a model could
        # put arbitrary (conversation-derived) text in the wrong field, and
        # ExtractionParseError's text ends up persisted on the job row,
        # which isn't subject to Memory's own private-content protections.
        candidate_type = candidate["type"]
        if not isinstance(candidate_type, str):
            raise ExtractionParseError(f"candidate {index} has a non-string type")
        if candidate_type not in _ALLOWED_TYPES:
            raise ExtractionParseError(f"candidate {index} has an unrecognized type value")

        title = candidate["title"]
        if not isinstance(title, str) or not title.strip():
            raise ExtractionParseError(f"candidate {index} has an empty/invalid title")
        if len(title) > MAX_TITLE_CHARS:
            raise ExtractionParseError(f"candidate {index} title exceeds {MAX_TITLE_CHARS} chars")

        content = candidate["content"]
        if not isinstance(content, str) or not content.strip():
            raise ExtractionParseError(f"candidate {index} has empty/invalid content")
        if len(content) > MAX_CONTENT_CHARS:
            raise ExtractionParseError(
                f"candidate {index} content exceeds {MAX_CONTENT_CHARS} chars"
            )

        parsed.append({"type": candidate_type, "title": title, "content": content})

    return parsed
