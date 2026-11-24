"""The pre-send compression stage (Phase 8B branch 6, brief D11).

`InferenceGateway.complete` calls `apply()` after budget and provider
selection and before the adapter call, on the sync and the stream path
alike. The stage may shorten long prior **assistant** replies through the
`compressor` sidecar (`deploy/compressor/`, Headroom behind a stdlib HTTP
server on an internal, egress-free network). Friday code never imports
Headroom: that package's base install pulls `litellm`, a second-door
library ADR-003 rules out (`ai_core/tests/test_no_headroom_import.py`).

It runs only when **all** of these hold:
- `settings.AI_COMPRESSION_ENABLED` is true (default false);
- the purpose is in `settings.AI_COMPRESSION_PURPOSES` (default empty);
- the purpose is not in `COMPRESSION_BYPASS` (a code constant: bypass wins
  over the setting);
- the purpose is in `COMPRESSION_ELIGIBLE` (fail closed: a purpose nobody
  classified is never compressed; the classification test fails on it).

What is sent: only messages with role `assistant`, never the system
message (core blocks, `self`, the memory block and fenced connector reads
live there), never a `user` message, never the last message of the list.
Friday's chat payload has no tool-role message (`assistant/context.py`), so
tool output is out of reach by construction.

Failure is open: a timeout (`AI_COMPRESSION_TIMEOUT_MS`), a connection
error, a non-200 answer or a malformed body sends the original messages and
records the error. The stage changes cost, never correctness.

Accounting: the call's single `ModelUsage` row carries
`context_report["compression"] = {applied, pre_tokens, post_tokens,
transforms, lossless, error, ...}`. The sidecar makes no model call (a
local transform), so it writes no `ModelUsage` of its own (rule 3).
"""

import json
import logging
import math
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

# --- purpose classification ---------------------------------------------------

# Never compressed, whatever AI_COMPRESSION_PURPOSES says (D11).
COMPRESSION_BYPASS = frozenset(
    {
        # approval and gate rendering: what the owner is asked to approve
        "harness.briefing",
        "harness.diff_summary",
        # tool-call and argument prompts
        "router.intent",
        "router.task_draft",
        "router.connector_args",
        "router.node_action_args",
        "agent.plan",
        "agent.skill_match",
        # trust checks and reviews
        "control.trust_check",
        "harness.review_summary",
        # learning prompts that check provenance
        "memory.episode_extraction",
        "memory.extraction",
        "memory.consolidation",
        "memory.fact_reconciliation",
        "memory.reflection",
        # the eval answer purpose, unconditionally: the compression arm of the
        # eval runs under the candidate purpose itself, never this one
        "memory.eval_answer",
        # Phase 12 D18: the morning digest's number check needs the exact
        # facts it was given
        "proactive.digest",
        # Phase 12 D15/D18: the skill matcher needs the exact approved names
        "skills.match",
        # Phase 12 D17/D18: the playbook distiller's quotes are checked
        # verbatim against the exact source fields it was given
        "skills.playbook_distill",
        # Phase 12 D11/D18: an async read's follow-up answers from the exact
        # fenced result it was given
        "chat.followup",
        # preflight probes a provider; its payload must be exactly as written
        "ops.preflight",
    }
)

# May be listed in AI_COMPRESSION_PURPOSES. `chat` is the only one whose
# payload carries multi-turn assistant history today (brief Q4); the others
# send a system and a user message, so the stage finds nothing to send.
COMPRESSION_ELIGIBLE = frozenset(
    {
        "chat",
        "facade.ephemeral",
        "agent.read_step",
    }
)

# Never pass through `complete()` with chat messages: embeddings, speech,
# and the external-CLI rows of `record_external_usage` (which sends nothing).
COMPRESSION_NOT_APPLICABLE = frozenset(
    {
        "memory.retrieval",
        "knowledge.embedding",
        "knowledge.retrieval",
        "voice.stt",
        "voice.tts",
        "harness.discovery",
        "harness.implementation",
        "harness.review",
    }
)


def classify(purpose):
    """`bypass`, `eligible`, `not_applicable` or `None` (unclassified)."""
    if purpose in COMPRESSION_BYPASS:
        return "bypass"
    if purpose in COMPRESSION_ELIGIBLE:
        return "eligible"
    if purpose in COMPRESSION_NOT_APPLICABLE:
        return "not_applicable"
    return None


def _enabled_purposes():
    return frozenset(getattr(settings, "AI_COMPRESSION_PURPOSES", ()) or ())


def is_enabled():
    return bool(getattr(settings, "AI_COMPRESSION_ENABLED", False))


def should_compress(purpose):
    """D11 gating: flag on, purpose listed, not bypassed, classified eligible."""
    return (
        is_enabled()
        and purpose in _enabled_purposes()
        and purpose not in COMPRESSION_BYPASS
        and purpose in COMPRESSION_ELIGIBLE
    )


# --- the sidecar client --------------------------------------------------------

MAX_REQUEST_BYTES = 1024 * 1024  # the sidecar refuses larger bodies too
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_TRANSFORMS = 20
_MAX_TRANSFORM_CHARS = 80


class CompressorError(Exception):
    """The sidecar did not give a usable answer. `code` is a short, content-free
    label recorded in the context report."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class CompressorClient:
    """`POST {url}` with `{"model", "messages"}`; expects `{"messages",
    "tokens_before", "tokens_after", "transforms_applied", "lossless"}`.

    The opener never uses an environment proxy: the sidecar sits on an
    internal compose network and a request must never leave through one."""

    def __init__(self, url, timeout_seconds):
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def compress(self, messages, *, model):
        body = json.dumps({"model": model, "messages": messages}).encode()
        if len(body) > MAX_REQUEST_BYTES:
            raise CompressorError("request_too_large")
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise CompressorError(f"http_{response.status}")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise CompressorError(f"http_{exc.code}") from None
        except TimeoutError:
            raise CompressorError("timeout") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise CompressorError("timeout") from None
            raise CompressorError("unreachable") from None
        except OSError:
            raise CompressorError("unreachable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CompressorError("response_too_large")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CompressorError("invalid_response") from None


def get_client():
    """The seam tests patch (`ai_core.compression.get_client`)."""
    return CompressorClient(
        settings.AI_COMPRESSION_URL,
        max(int(settings.AI_COMPRESSION_TIMEOUT_MS), 1) / 1000,
    )


# --- the stage -----------------------------------------------------------------


def estimate_tokens(text):
    """Local estimate (4 chars/token) used only when the sidecar gave no count."""
    return math.ceil(len(text) / 4)


def compressible_indices(messages):
    """Indexes of the messages the sidecar may see: `assistant` messages with
    string content, never the last message of the list."""
    last = len(messages) - 1
    return [
        index
        for index, message in enumerate(messages)
        if index != last
        and isinstance(message, dict)
        and message.get("role") == "assistant"
        and isinstance(message.get("content"), str)
        and message["content"]
    ]


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CompressorError("invalid_response")
    return value


def _validated(answer, originals):
    """The sidecar's answer, checked: one assistant string message per sent
    message, never longer than the original, sane counts."""
    if not isinstance(answer, dict):
        raise CompressorError("invalid_response")
    returned = answer.get("messages")
    if not isinstance(returned, list) or len(returned) != len(originals):
        raise CompressorError("invalid_response")
    contents = []
    for sent, got in zip(originals, returned, strict=True):
        if (
            not isinstance(got, dict)
            or got.get("role") != "assistant"
            or not isinstance(got.get("content"), str)
        ):
            raise CompressorError("invalid_response")
        # Never inflate a message: keep the original when the answer is longer.
        text = got["content"]
        contents.append(text if len(text) <= len(sent["content"]) else sent["content"])
    before = _count(answer.get("tokens_before"))
    after = _count(answer.get("tokens_after"))
    transforms = answer.get("transforms_applied") or []
    if not isinstance(transforms, list):
        raise CompressorError("invalid_response")
    transforms = [str(t)[:_MAX_TRANSFORM_CHARS] for t in transforms[:_MAX_TRANSFORMS]]
    lossless = answer.get("lossless")
    lossless = lossless if isinstance(lossless, bool) else None
    return contents, before, min(after, before), transforms, lossless


def apply(purpose, messages, *, model, personal_data=False):
    """`(messages_to_send, report_or_None)`.

    `None` report: the stage did not run (flag off, purpose not listed) and
    the messages are returned unchanged -- the same list object, so the
    payload is byte-identical to a call without the stage. Otherwise the
    report goes into the call's context report under `compression`, and the
    returned list is a copy; the caller's list is never mutated."""
    if not is_enabled() or purpose not in _enabled_purposes():
        return messages, None
    report = {
        "applied": False,
        "pre_tokens": 0,
        "post_tokens": 0,
        "transforms": [],
        "lossless": None,
        "error": "",
    }
    if personal_data:
        # D11: allowed -- the sidecar is local with no egress. Recorded so
        # the row shows a personal-class payload went through the stage.
        report["personal_data"] = True
    if not should_compress(purpose):
        report["skipped"] = "bypass" if purpose in COMPRESSION_BYPASS else "not_eligible"
        return messages, report

    indices = compressible_indices(messages)
    if not indices:
        report["skipped"] = "no_assistant_history"
        return messages, report

    originals = [{"role": "assistant", "content": messages[i]["content"]} for i in indices]
    estimate = sum(estimate_tokens(m["content"]) for m in originals)
    report["sent_messages"] = len(originals)
    try:
        answer = get_client().compress(originals, model=model or "")
        contents, before, after, transforms, lossless = _validated(answer, originals)
    except CompressorError as exc:
        logger.warning("compressor sidecar failed (%s); sending the original messages", exc.code)
        report.update(pre_tokens=estimate, post_tokens=estimate, error=exc.code)
        return messages, report
    except Exception:  # never let the stage fail a model call
        logger.exception("compressor stage failed; sending the original messages")
        report.update(pre_tokens=estimate, post_tokens=estimate, error="stage_error")
        return messages, report

    compressed = [dict(m) if isinstance(m, dict) else m for m in messages]
    changed = False
    for index, text in zip(indices, contents, strict=True):
        if text != compressed[index]["content"]:
            compressed[index]["content"] = text
            changed = True
    report.update(
        applied=changed,
        pre_tokens=before,
        post_tokens=after if changed else before,
        transforms=transforms,
        lossless=lossless,
    )
    return (compressed if changed else messages), report
