"""The node action trust check (Phase 10 brief D9, the "Nod" pattern).

`check_node_action(*, workspace, actor, source_message_id, capability,
args) -> TrustVerdict(verdict in {ok, warn, block}, reason_code,
evidence_modes)` compares a proposed node action with what the owner
actually typed or said. It can **warn or block**; it never approves, never
creates or decides an approval and never shortens a step. Its only consumer
is `tools.node_actions.precheck_request` (through the port registered by
`assistant.apps.AssistantConfig.ready()`), whose `prepare_payload` refuses a
`block` -- that refusal is the one effect a verdict can have.

Evidence (D9): only `user`-role messages of the source message's
conversation, authored by the actor, in the last 30 minutes, at most 10,
each labelled `typed` or `voice` from `Message.input_mode`. Never assistant
text, tool output, node output, transcripts or memory -- the query below can
see nothing else.

Deterministic rules first (no model call):
- the source message is missing, not in this workspace, not the actor's,
  not a user message, or older than 10 minutes -> `block`
  (`no_owner_evidence`), and no model call is made;
- the source turn was voice -> the best possible verdict is `warn`
  (`voice_evidence`, ADR-015 decision 6);
- for `repo.command.run`, the mapped repo name or a path segment of
  `repo_path` must appear in the evidence, else `warn`
  (`repo_not_mentioned`).

Model step: one gateway `complete`, purpose `control.trust_check` (cheap
tier, the `control` budget), the evidence fenced as owner text and the
proposal as data, a strict JSON answer `{verdict, reason_code}` from a
closed list. `inconsistent -> block`, `unclear -> warn`, `consistent -> ok`,
still capped by the rules. A parse failure, provider error, budget refusal
or missing prompt -> `warn` (`trust_check_unavailable`): the check is an
extra signal; the two human acts remain the gate. The gateway writes exactly
one `ModelUsage` row per call, with the prompt version and a content-free
`context_report` (evidence count and modes).

The prompt pack is versioned in `docs/prompts/phase-10-control/trust-check.md`
and seeded as the `friday_control_trust_check` template by
`seed_ai_defaults`.
"""

import json
import logging
import posixpath
from datetime import timedelta

from django.utils import timezone

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from assistant.models import Conversation, Message
from memory.context import neutralize_fence, strip_invisible_formatting
from tools.node_action_ports import TrustVerdict

logger = logging.getLogger(__name__)

PROMPT_NAME = "friday_control_trust_check"
EVIDENCE_WINDOW = timedelta(minutes=30)
SOURCE_MAX_AGE = timedelta(minutes=10)
MAX_EVIDENCE_MESSAGES = 10
MAX_EVIDENCE_CHARS = 2000
REPO_CAPABILITY = "repo.command.run"

MODE_TYPED = "typed"
MODE_VOICE = "voice"

# The model step's closed vocabulary (pinned equal to the port's by a test).
MODEL_VERDICTS = {"consistent": "ok", "unclear": "warn", "inconsistent": "block"}
MODEL_REASON_CODES = frozenset(
    {
        "matches_request",
        "different_action",
        "different_target",
        "not_requested",
        "ambiguous_request",
    }
)
_ALLOWED_PAIRS = {
    "consistent": frozenset({"matches_request"}),
    "unclear": frozenset(
        {"ambiguous_request", "different_action", "different_target", "not_requested"}
    ),
    "inconsistent": frozenset({"different_action", "different_target", "not_requested"}),
}

# Fence words: neither block may close (or open) its own tag, or the other's.
_FENCE_WORDS = ("owner_message", "proposal")
_SEVERITY = {"ok": 0, "warn": 1, "block": 2}


class TrustCheckParseError(Exception):
    """The model's answer was not one allowed `{verdict, reason_code}`."""


def parse_trust_answer(raw_text):
    """Strict parse: exactly one JSON object with exactly the keys
    `verdict` and `reason_code`, an allowed verdict and an allowed reason for
    it. Returns `(verdict, reason_code)`."""
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TrustCheckParseError("invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"verdict", "reason_code"}:
        raise TrustCheckParseError("unexpected shape")
    verdict, reason = payload["verdict"], payload["reason_code"]
    if not isinstance(verdict, str) or verdict not in _ALLOWED_PAIRS:
        raise TrustCheckParseError("unknown verdict")
    if not isinstance(reason, str) or reason not in _ALLOWED_PAIRS[verdict]:
        raise TrustCheckParseError("reason not allowed for verdict")
    return verdict, reason


def _source_message(workspace, actor, source_message_id):
    """The source message when it is the actor's own user message in this
    workspace, else None (no owner evidence)."""
    if not source_message_id or workspace is None or actor is None:
        return None
    if not getattr(actor, "is_authenticated", False):
        return None
    try:
        return (
            Message.objects.for_actor(actor, workspace=workspace)
            .select_related("conversation")
            .filter(
                pk=source_message_id,
                role=Message.Role.USER,
                created_by_id=actor.pk,
                conversation__workspace_id=workspace.pk,
            )
            .first()
        )
    except Exception:  # a malformed id is simply no evidence
        return None


def input_mode_of(*, workspace, actor, source_message_id):
    """The evidence source port (D6/D9): the source message's input mode
    (`text`/`voice`) when it is the actor's own user message in this
    workspace, else None (recorded as `unknown`)."""
    message = _source_message(workspace, actor, source_message_id)
    if message is None:
        return None
    return Message.InputMode.VOICE if message.input_mode == Message.InputMode.VOICE else "text"


def _label(message):
    return MODE_VOICE if message.input_mode == Message.InputMode.VOICE else MODE_TYPED


def collect_evidence(*, workspace, actor, source, now=None):
    """The owner messages the check may see (D9): user role, the actor's
    own, this conversation, the last 30 minutes, at most 10, oldest first.
    Never assistant text: the role filter is the whole guarantee."""
    now = now or timezone.now()
    rows = list(
        Message.objects.for_actor(actor, workspace=workspace)
        .filter(
            conversation_id=source.conversation_id,
            role=Message.Role.USER,
            created_by_id=actor.pk,
            created_at__gte=now - EVIDENCE_WINDOW,
            created_at__lte=now,
        )
        .order_by("-created_at", "-id")[:MAX_EVIDENCE_MESSAGES]
    )
    rows.reverse()
    return rows


def _clean(text):
    text = strip_invisible_formatting(str(text or "").replace("\r", ""))[:MAX_EVIDENCE_CHARS]
    return neutralize_fence(text, *_FENCE_WORDS)


def render_evidence(evidence):
    lines = ["<owner_messages>"]
    for message in evidence:
        lines.append(f'<owner_message mode="{_label(message)}">{_clean(message.content)}')
        lines.append("</owner_message>")
    lines.append("</owner_messages>")
    return "\n".join(lines)


def render_proposal(capability, args):
    body = json.dumps({"capability": capability, "args": args}, ensure_ascii=False, sort_keys=True)
    return f"<proposal>\n{_clean(body)}\n</proposal>"


def _repo_terms(args):
    """The mapped repo name (the last segment, as `describe_request` shows
    it) and every path segment of `repo_path`, casefolded."""
    path = args.get("repo_path") if isinstance(args, dict) else None
    if not isinstance(path, str):
        return set()
    segments = [s for s in path.strip("/").split("/") if s not in ("", "~", ".", "..")]
    terms = {s.casefold() for s in segments if len(s) >= 2}
    name = posixpath.basename(path.rstrip("/"))
    if len(name) >= 2:
        terms.add(name.casefold())
    return terms


def repo_mentioned(evidence, args) -> bool:
    terms = _repo_terms(args)
    if not terms:
        return False
    text = " ".join(str(m.content or "") for m in evidence).casefold()
    return any(term in text for term in terms)


def _prompt_version():
    template = PromptTemplate.objects.filter(name=PROMPT_NAME).select_related("active_version")
    template = template.first()
    if template is None or template.active_version_id is None:
        return None
    return template.active_version


def _personal_data(conversation_id):
    return bool(
        Conversation.objects.filter(pk=conversation_id)
        .values_list("personal_data", flat=True)
        .first()
    )


def _model_step(*, workspace, actor, source, evidence, capability, args, modes):
    """One `control.trust_check` call; returns `(verdict, reason)` or None
    (unavailable)."""
    prompt_version = _prompt_version()
    if prompt_version is None:
        logger.warning("trust check prompt %s is not configured", PROMPT_NAME)
        return None
    messages = [
        {"role": "system", "content": prompt_version.content},
        {
            "role": "user",
            "content": render_evidence(evidence) + "\n\n" + render_proposal(capability, args),
        },
    ]
    report = {
        "trust_check": {
            "prompt": PROMPT_NAME,
            "prompt_version": prompt_version.version,
            "evidence_count": len(evidence),
            "evidence_modes": list(modes),
            "capability": capability,
        }
    }
    try:
        result = InferenceGateway().complete(
            purpose="control.trust_check",
            messages=messages,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            stream=False,
            context_report=report,
            personal_data=_personal_data(source.conversation_id),
        )
    except GatewayError as exc:
        logger.info("trust check unavailable: %s", exc.error_type)
        return None
    try:
        return parse_trust_answer(result.text)
    except TrustCheckParseError:
        logger.info("trust check answer did not parse")
        return None


def _worst(candidates):
    """The most severe `(verdict, reason)`; on a tie the earliest wins (the
    caller lists rule outcomes before the model's)."""
    best = candidates[0]
    for candidate in candidates[1:]:
        if _SEVERITY[candidate[0]] > _SEVERITY[best[0]]:
            best = candidate
    return best


def check_node_action(*, workspace, actor, source_message_id, capability, args, now=None):
    """The D9 trust check. It warns or blocks; it never approves."""
    now = now or timezone.now()
    source = _source_message(workspace, actor, source_message_id)
    if source is None or source.created_at < now - SOURCE_MAX_AGE or source.created_at > now:
        return TrustVerdict("block", "no_owner_evidence")
    evidence = collect_evidence(workspace=workspace, actor=actor, source=source, now=now)
    if source.pk not in {m.pk for m in evidence}:
        evidence = [*evidence[-(MAX_EVIDENCE_MESSAGES - 1) :], source]
    modes = tuple(sorted({_label(m) for m in evidence}))
    # Rule outcomes first, so a tie names the deterministic reason.
    candidates = []
    if _label(source) == MODE_VOICE:
        candidates.append(("warn", "voice_evidence"))
    if capability == REPO_CAPABILITY and not repo_mentioned(evidence, args):
        candidates.append(("warn", "repo_not_mentioned"))
    answer = _model_step(
        workspace=workspace,
        actor=actor,
        source=source,
        evidence=evidence,
        capability=capability,
        args=args,
        modes=modes,
    )
    if answer is None:
        candidates.append(("warn", "trust_check_unavailable"))
    else:
        verdict, reason = answer
        mapped = MODEL_VERDICTS[verdict]
        candidates.append((mapped, "consistent" if mapped == "ok" else reason))
    verdict, reason = _worst(candidates)
    return TrustVerdict(verdict, reason, modes)
