"""The morning digest's Speak step (Phase 12 D4, D6).

Runs inside the schedule's unattended context, after the Collect step,
and only when a probe found something. It:

1. turns the probe results into `sections` -- per D6 section, in order,
   each non-empty probe's count and its D1 labels (the facts);
2. makes **one** gateway call, purpose `proactive.digest`,
   `personal_data=True` (calendar and personal tasks are personal-class,
   so only `personal_data_allowed` providers serve it), with the seeded
   prompt and the facts fenced as `<digest_facts>`;
3. cleans the model's text (Markdown and invisible formatting stripped,
   length-bounded) and runs the number check (`proactive.number_check`):
   any number not in the facts discards the text;
4. otherwise -- no prompt, no eligible provider (checked first, so no call
   and no `ModelUsage` row), a budget refusal, a provider error, an empty
   answer, a failed number check -- uses the Friday template
   (`composed_by=template`), and the run still succeeds;
5. writes the `DigestRun` row (the run's own row).

Nothing here approves, sends, runs or creates anything else.
"""

import logging
import re
import unicodedata

from django.conf import settings
from django.utils import timezone

from ai_core.exceptions import BudgetExceeded, GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from core import probes
from proactive import number_check, templates
from proactive.models import DigestRun

logger = logging.getLogger(__name__)

PURPOSE = "proactive.digest"
PROMPT_NAME = "friday_proactive_digest"
FENCE = "digest_facts"
_WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


def build_sections(results):
    """`results`: [(ProbeSpec, ProbeResult)] in registry order. Returns the
    D6 sections, in section order, empty probes and sections omitted."""
    by_section = {}
    for spec, result in results:
        if not result.count:
            continue
        entry = {
            "probe": spec.name,
            "name": templates.fact_name(spec.name),
            "count": int(result.count),
            "link": spec.link,
            "items": [
                {"label": item.label, "link": item.link or spec.link} for item in result.items
            ],
        }
        by_section.setdefault(spec.digest_section, []).append(entry)
    return [
        {"section": section, "title": templates.section_title(section), "entries": entries}
        for section in probes.DIGEST_SECTIONS
        if (entries := by_section.get(section))
    ]


def render_facts(sections, *, now) -> str:
    """The fenced facts block: the local date, then per section its facts
    and labels. Labels are D1 labels (validated: no markup), so nothing
    inside can close the fence."""
    local = timezone.localtime(now)
    lines = [f"tarih: {local.date().isoformat()} ({_WEEKDAYS[local.weekday()]})"]
    for section in sections:
        lines.append(f"[{section['title']}]")
        for entry in section["entries"]:
            lines.append(f"- {entry['name']}: {entry['count']}")
            lines.extend(f"  - {item['label']}" for item in entry["items"])
    body = "\n".join(lines)
    return f"<{FENCE}>\n{body}\n</{FENCE}>"


_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"[*_`#>|~]+")
_BULLET = re.compile(r"^\s*(?:[-+•]|\d+[.)])\s+", re.MULTILINE)


def clean_text(text: str, *, max_chars: int) -> str:
    """Speakable plain text: invisible formatting (Unicode `Cf`) and
    control characters dropped, Markdown links reduced to their text,
    emphasis/heading/quote marks and list bullets removed, whitespace
    collapsed; bounded at a sentence end within `max_chars` where possible."""
    text = unicodedata.normalize("NFC", str(text or ""))
    text = "".join(
        ch
        for ch in text
        if unicodedata.category(ch) != "Cf" and (ch in "\n\t" or unicodedata.category(ch) != "Cc")
    )
    text = _MD_LINK.sub(r"\1", text)
    text = _BULLET.sub("", text)
    text = _MD_MARKS.sub("", text)
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 0 else cut.rstrip()


def _prompt_version():
    template = (
        PromptTemplate.objects.select_related("active_version").filter(name=PROMPT_NAME).first()
    )
    return template.active_version if template is not None else None


def _model_text(*, workspace, owner, facts, sections):
    """(text, fallback_reason, usage_id). `text` is None on any fallback."""
    prompt = _prompt_version()
    if prompt is None:
        return None, DigestRun.FallbackReason.NO_PROMPT, None
    gateway = InferenceGateway()
    if not gateway.has_eligible_provider(purpose=PURPOSE, personal_data=True):
        return None, DigestRun.FallbackReason.NO_PROVIDER, None
    usage_ids = []
    report = {
        "proactive": {
            "sections": [section["section"] for section in sections],
            "facts": sum(len(section["entries"]) for section in sections),
        }
    }
    messages = [
        {"role": "system", "content": prompt.content},
        {"role": "user", "content": facts},
    ]
    try:
        result = gateway.complete(
            PURPOSE,
            messages,
            actor=owner,
            workspace=workspace,
            prompt_version=prompt,
            personal_data=True,
            context_report=report,
            on_usage=lambda usage: usage_ids.append(usage.pk),
        )
    except BudgetExceeded:
        return None, DigestRun.FallbackReason.BUDGET_EXCEEDED, _first(usage_ids)
    except GatewayError as exc:
        logger.info("Digest composition unavailable (%s)", exc.error_type)
        reason = (
            DigestRun.FallbackReason.NO_PROVIDER
            if exc.error_type == "no_provider"
            else DigestRun.FallbackReason.MODEL_ERROR
        )
        return None, reason, _first(usage_ids)
    usage_id = _first(usage_ids) or getattr(result.usage, "pk", None)
    return result.text, DigestRun.FallbackReason.NONE, usage_id


def _first(values):
    return values[0] if values else None


def compose_digest(*, run, schedule, results, now):
    """The Speak step. Returns the `DigestRun` (always: the template keeps
    the digest working with no provider)."""
    workspace = schedule.workspace
    max_chars = int(settings.PROACTIVE_DIGEST_MAX_CHARS)
    sections = build_sections(results)
    facts = render_facts(sections, now=now)
    template_text = templates.render(sections, max_chars=max_chars)
    raw, reason, usage_id = _model_text(
        workspace=workspace, owner=workspace.owner, facts=facts, sections=sections
    )
    text = None
    if raw is not None:
        cleaned = clean_text(raw, max_chars=max_chars)
        if not cleaned:
            reason = DigestRun.FallbackReason.EMPTY
        elif not number_check.passes(cleaned, number_check.allowed_numbers(facts)):
            reason = DigestRun.FallbackReason.NUMBER_CHECK_FAILED
        else:
            text = cleaned
    return DigestRun.objects.create(
        workspace=workspace,
        run=run,
        schedule=schedule,
        sections=sections,
        spoken_text=text if text is not None else template_text,
        composed_by=(
            DigestRun.ComposedBy.MODEL if text is not None else DigestRun.ComposedBy.TEMPLATE
        ),
        fallback_reason=reason if text is None else DigestRun.FallbackReason.NONE,
        model_usage_id=usage_id,
        data_class=probes.data_class_of(workspace),
    )
