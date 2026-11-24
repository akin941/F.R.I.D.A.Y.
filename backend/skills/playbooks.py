"""Procedural playbooks distilled from harness runs (Phase 12 D17, ADR-016
decision 5, ADR-012 note).

**Selection.** Nightly (`skills.tasks.distill_playbooks`, a static beat
entry), per workspace and repo: a repo with at least
`SKILLS_PLAYBOOK_MIN_SOURCES` unused `PlaybookSource` rows (the most recent
`SKILLS_PLAYBOOK_MAX_SOURCES` of them) gets **one** gateway call, purpose
`skills.playbook_distill`, `personal_data` by the workspace's data class --
unless the workspace already proposed `SKILLS_PLAYBOOK_WEEKLY_CAP`
playbooks in the last seven days or holds `SKILLS_MAX_PENDING_PLAYBOOK`
pending ones (no call; counted).

**Provenance in code** (`verify_steps`). Every step of the draft must name
a `source` job from the input set and carry a `quote` that, after
whitespace normalisation, is a verbatim span of **that** source's
owner-typed goal or its `test_command` (the job's snapshot of the
owner-configured repo test command) -- the only quotable fields. A quote
found only among the changed paths (agent-chosen strings) or in the
context text (model-written summaries) is not verified: the step is
dropped, and paths survive only as hints shown "unverified". Fewer than
`SKILLS_PLAYBOOK_MIN_STEPS` verified steps drop the draft (counted). The
drafted SKILL.md must pass the SKILL.md parser and its refusals.

**Proposal, never more.** A kept draft becomes a `proposed` version
(origin `playbook`) through `skills.ports.propose_version`, with the
step <-> job map as its provenance; the owner decides it on `/skills`
(D14). Nothing here approves, activates or uses a skill.

**Unattended.** Each repo's call runs inside
`unattended_run(allowed_purposes={"skills.playbook_distill"})` (D2):
every other effect -- approvals, tasks, memory, connectors, nodes,
harness jobs, other gateway purposes, Celery dispatch, owner skill
decisions -- is denied in its owning service and audited through the
`DenialRecorder`, flushed after each step and in a `finally`. Besides its
own run rows and the sources' `distill_run` mark, the run writes only the
proposal.

A distillation that got an answer from the model consumes its input
sources whatever the outcome (a dropped draft is not retried every night
on the same input); a skipped, refused or failed call leaves them unused.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ai_core.exceptions import BudgetExceeded, GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from audit.unattended import DenialRecorder
from core.unattended import UnattendedDenied, unattended_run, unattended_step
from skills import ports
from skills.models import (
    InstructionSkill,
    InstructionSkillVersion,
    PlaybookDistillRun,
    PlaybookSource,
)
from skills.skill_md import (
    MAX_DESCRIPTION_CHARS,
    NAME_RE,
    render_skill_md,
    strip_invisible,
)
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

PURPOSE = "skills.playbook_distill"
PROMPT_NAME = "friday_skills_playbook_distill"
RUN_SOURCE = "skills.playbook_distill"
ALLOWED_PURPOSES = frozenset({PURPOSE})

Outcome = PlaybookDistillRun.Outcome

STEP_TEXT_MAX_CHARS = 300
QUOTE_MAX_CHARS = 300
STEP_PATHS_MAX = 5
UNVERIFIED_PATHS_MAX = 10
PROMPT_PATHS_MAX = 40

# Drop reasons (closed tokens, counted on the run row).
DROP_MALFORMED = "malformed_step"
DROP_EMPTY_TEXT = "empty_text"
DROP_UNKNOWN_SOURCE = "unknown_source"
DROP_NO_QUOTE = "no_quote"
DROP_QUOTE_IS_PATH = "quote_is_path"
DROP_QUOTE_FROM_CONTEXT = "quote_from_context"
DROP_QUOTE_NOT_FOUND = "quote_not_found"
DROP_QUOTE_TOO_SHORT = "quote_too_short"
DROP_OVER_LIMIT = "over_limit"

FIELD_GOAL = "goal"
FIELD_TEST_COMMAND = "test_command"
FIELD_LABELS = {FIELD_GOAL: "hedef", FIELD_TEST_COMMAND: "test komutu"}

_WS_RE = re.compile(r"\s+")


class PlaybookParseError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedStep:
    text: str
    source: str
    field: str
    quote: str
    paths: tuple = ()


@dataclass
class Verification:
    steps: list = field(default_factory=list)
    dropped: dict = field(default_factory=dict)
    unverified_paths: list = field(default_factory=list)

    def drop(self, reason):
        self.dropped[reason] = self.dropped.get(reason, 0) + 1


def normalize(text) -> str:
    """Whitespace normalisation for the provenance check: invisible
    formatting stripped, NFC, every run of whitespace one space, trimmed."""
    if not isinstance(text, str):
        return ""
    return _WS_RE.sub(" ", strip_invisible(text)).strip()


def parse_draft(raw_text):
    """`{"playbook": null}` -> None; `{"name", "description", "steps"}` ->
    the dict. Anything else raises `PlaybookParseError`. Steps are checked
    one by one later (`verify_steps`)."""
    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError) as exc:
        raise PlaybookParseError("invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PlaybookParseError("not an object")
    if set(payload) == {"playbook"}:
        if payload["playbook"] is None:
            return None
        raise PlaybookParseError("unexpected playbook value")
    if set(payload) != {"name", "description", "steps"}:
        raise PlaybookParseError("unexpected keys")
    if not isinstance(payload["name"], str) or not isinstance(payload["description"], str):
        raise PlaybookParseError("name and description must be strings")
    if not isinstance(payload["steps"], list):
        raise PlaybookParseError("steps must be a list")
    return payload


def _clean_paths(values):
    if not isinstance(values, list):
        return None
    if not all(isinstance(value, str) for value in values):
        return None
    return values


def _match_field(quote, source):
    """(field, reason): which quotable field of `source` holds `quote`
    verbatim (after normalisation), or why none does."""
    minimum = int(settings.SKILLS_PLAYBOOK_MIN_QUOTE_CHARS)
    for name, value in ((FIELD_GOAL, source.goal), (FIELD_TEST_COMMAND, source.test_command)):
        normalized = normalize(value)
        if normalized and quote in normalized:
            if len(quote) < min(minimum, len(normalized)):
                return None, DROP_QUOTE_TOO_SHORT
            return name, ""
    if any(quote in normalize(path) for path in source.changed_paths or []):
        return None, DROP_QUOTE_IS_PATH
    if quote in normalize(source.context_text):
        return None, DROP_QUOTE_FROM_CONTEXT
    return None, DROP_QUOTE_NOT_FOUND


def _source_paths(raw_paths, source):
    known = set(ports.strip_skill_paths(source.changed_paths))
    kept = []
    for path in raw_paths or []:
        if path in known and path not in kept:
            kept.append(path)
    return kept


def verify_steps(raw_steps, sources) -> Verification:
    """The provenance check (D17). `sources` maps job id (str) to its
    `PlaybookSource`. Returns the verified steps (at most
    `SKILLS_PLAYBOOK_MAX_STEPS`), the drop counts by reason and the
    unverified path hints."""
    by_id = {str(key): value for key, value in sources.items()}
    result = Verification()
    limit = int(settings.SKILLS_PLAYBOOK_MAX_STEPS)
    for raw in raw_steps:
        if (
            not isinstance(raw, dict)
            or not set(raw) <= {"text", "source", "quote", "paths"}
            or not {"text", "source", "quote"} <= set(raw)
            or not all(isinstance(raw[key], str) for key in ("text", "source", "quote"))
        ):
            result.drop(DROP_MALFORMED)
            continue
        paths = _clean_paths(raw.get("paths", []))
        if paths is None:
            result.drop(DROP_MALFORMED)
            continue
        text = normalize(raw["text"])[:STEP_TEXT_MAX_CHARS]
        if not text:
            result.drop(DROP_EMPTY_TEXT)
            continue
        source = by_id.get(raw["source"].strip())
        if source is None:
            result.drop(DROP_UNKNOWN_SOURCE)
            continue
        quote = normalize(raw["quote"])
        if not quote:
            result.drop(DROP_NO_QUOTE)
            continue
        if len(quote) > QUOTE_MAX_CHARS:
            result.drop(DROP_QUOTE_NOT_FOUND)
            continue
        field_name, reason = _match_field(quote, source)
        hints = _source_paths(paths, source)
        if field_name is None:
            result.drop(reason)
            if reason == DROP_QUOTE_IS_PATH:
                # The path itself survives only as an unverified hint.
                for path in _source_paths(
                    [p for p in source.changed_paths or [] if quote in normalize(p)] + hints,
                    source,
                ):
                    if path not in result.unverified_paths:
                        result.unverified_paths.append(path)
            continue
        if len(result.steps) >= limit:
            result.drop(DROP_OVER_LIMIT)
            continue
        result.steps.append(
            VerifiedStep(
                text=text,
                source=str(source.job_id),
                field=field_name,
                quote=quote,
                paths=tuple(hints[:STEP_PATHS_MAX]),
            )
        )
    result.unverified_paths = result.unverified_paths[:UNVERIFIED_PATHS_MAX]
    return result


def _slug(text):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:40].strip("-")


def skill_name(raw_name, repo_label, *, reserved=frozenset()):
    """The model's name when it is a valid skill name that no owner (or
    curator) skill of the workspace already uses, else a Friday-made one
    from the (owner-configured) repo label: a playbook never proposes a new
    version of a skill it did not create."""
    candidate = normalize(raw_name).lower()
    if NAME_RE.match(candidate) and candidate not in reserved:
        return candidate
    slug = _slug(repo_label)
    name = f"playbook-{slug}" if slug else "playbook"
    return name if NAME_RE.match(name) else "playbook"


def _code(text):
    return "`" + text.replace("`", "'") + "`"


def render_body(*, repo_label, verification, source_count):
    """The playbook's SKILL.md body: Friday-authored framing around the
    verified steps, each with its source job and verbatim quote; agent
    paths are labelled unverified."""
    lines = [
        f"Bu playbook, {_code(repo_label)} deposunda onaylanmış ve testleri geçmiş "
        f"{source_count} harness işinden çıkarıldı. Her adım bir işe ve o işin sahip "
        "tarafından yazılmış hedefinden ya da test komutundan birebir bir alıntıya "
        "bağlıdır. Dosya ipuçlarını kodlama ajanı seçti; doğrulanmamıştır.",
        "",
        "## Adımlar",
        "",
    ]
    for index, step in enumerate(verification.steps, start=1):
        quote = step.quote.replace('"', "'")
        lines.append(f"{index}. {step.text}")
        lines.append(f'   - Kaynak: iş {step.source} ({FIELD_LABELS[step.field]}): "{quote}"')
        if step.paths:
            hint = ", ".join(_code(path) for path in step.paths)
            lines.append(f"   - Dosya ipuçları (doğrulanmamış): {hint}")
    if verification.unverified_paths:
        lines += ["", "## Doğrulanmamış dosya ipuçları", ""]
        lines += [f"- {_code(path)}" for path in verification.unverified_paths]
    return "\n".join(lines)


def reserved_names(workspace):
    """Names of the workspace's skills that a playbook did not create."""
    return frozenset(
        InstructionSkill.objects.filter(workspace=workspace)
        .exclude(origin=InstructionSkill.Origin.PLAYBOOK)
        .values_list("name", flat=True)
    )


def render_draft(*, draft, repo_label, verification, source_count, reserved=frozenset()):
    name = skill_name(draft["name"], repo_label, reserved=reserved)
    description = normalize(draft["description"])[:MAX_DESCRIPTION_CHARS]
    if not description:
        description = f"{repo_label} deposunda tekrar eden işler için adımlar."[
            :MAX_DESCRIPTION_CHARS
        ]
    front_matter = {
        "name": name,
        "description": description,
        "metadata": {
            "playbook-repo": normalize(repo_label)[:100] or "repo",
            "playbook-sources": str(source_count),
        },
    }
    body = render_body(
        repo_label=normalize(repo_label)[:100] or "repo",
        verification=verification,
        source_count=source_count,
    )
    return render_skill_md(front_matter, body)


def _fence(text):
    # A source cannot close the block it sits in.
    return re.sub(
        r"</?\s*(source|untrusted_context|goal|test_command)",
        lambda m: m.group(0).replace("<", "&lt;"),
        text,
        flags=re.I,
    )


def render_sources(sources):
    """The user message: one `<source>` block per job; the quotable fields
    first, then the fenced, never-quotable context."""
    blocks = []
    for source in sources:
        paths = ports.strip_skill_paths(source.changed_paths)[:PROMPT_PATHS_MAX]
        context = json.dumps(
            {"changed_paths": paths, "summaries": source.context_text},
            ensure_ascii=False,
        )
        blocks.append(
            f'<source id="{source.job_id}">\n'
            f"<goal>\n{_fence(source.goal)}\n</goal>\n"
            f"<test_command>\n{_fence(source.test_command)}\n</test_command>\n"
            f"<untrusted_context>\n{_fence(context)}\n</untrusted_context>\n"
            "</source>"
        )
    return "\n\n".join(blocks)


def _prompt_version():
    template = (
        PromptTemplate.objects.filter(name=PROMPT_NAME).select_related("active_version").first()
    )
    if template is None or template.active_version_id is None:
        return None
    return template.active_version


def unused_sources(workspace, repo_key):
    return PlaybookSource.objects.filter(
        workspace=workspace, repo_key=repo_key, distill_run__isnull=True
    ).order_by("-created_at", "-id")


def eligible_repos(workspace):
    minimum = int(settings.SKILLS_PLAYBOOK_MIN_SOURCES)
    keys = (
        PlaybookSource.objects.filter(workspace=workspace, distill_run__isnull=True)
        .values_list("repo_key", flat=True)
        .distinct()
    )
    return sorted(key for key in keys if unused_sources(workspace, key).count() >= minimum)


def proposals_this_week(workspace, now):
    return PlaybookDistillRun.objects.filter(
        workspace=workspace,
        outcome=Outcome.PROPOSED,
        created_at__gte=now - timedelta(days=7),
    ).count()


def pending_playbook_proposals(workspace):
    return InstructionSkillVersion.objects.filter(
        workspace=workspace,
        decision=InstructionSkillVersion.Decision.PENDING,
        proposed_by=InstructionSkillVersion.ProposedBy.PLAYBOOK,
    ).count()


def _personal_data(workspace):
    return workspace.type == "personal"


def _record(run_id, *, workspace, repo_key, outcome, reason="", counts=None, usage_id=None):
    return PlaybookDistillRun.objects.create(
        id=run_id,
        workspace=workspace,
        repo_key=repo_key,
        outcome=outcome,
        reason=reason[:40],
        counts=counts or {},
        model_usage_id=usage_id,
    )


def _call_model(*, workspace, prompt_version, sources, report, usage_ids):
    messages = [
        {"role": "system", "content": prompt_version.content},
        {"role": "user", "content": render_sources(sources)},
    ]
    return InferenceGateway().complete(
        PURPOSE,
        messages,
        actor=workspace.owner,
        workspace=workspace,
        prompt_version=prompt_version,
        stream=False,
        context_report=report,
        personal_data=_personal_data(workspace),
        on_usage=lambda usage: usage_ids.append(usage.pk),
    )


def _propose(*, run_id, workspace, repo_key, sources, raw_text, usage_id):
    """Parse, verify, render and propose (one `atomic()` block). Returns the
    run row. The sources are consumed whatever the outcome."""
    counts = {"sources": len(sources)}
    by_id = {str(source.job_id): source for source in sources}
    repo_label = sources[0].repo_label
    outcome, reason, version_id = Outcome.DROPPED, "", None
    with transaction.atomic():
        try:
            draft = parse_draft(raw_text)
        except PlaybookParseError:
            draft, reason = None, "parse_error"
        if draft is None and not reason:
            reason = "no_playbook"
        if draft is not None:
            verification = verify_steps(draft["steps"], by_id)
            counts.update(
                {
                    "steps_total": len(draft["steps"]),
                    "steps_verified": len(verification.steps),
                    "dropped": verification.dropped,
                    "unverified_paths": len(verification.unverified_paths),
                }
            )
            if len(verification.steps) < int(settings.SKILLS_PLAYBOOK_MIN_STEPS):
                reason = "too_few_steps"
            else:
                text = render_draft(
                    draft=draft,
                    repo_label=repo_label,
                    verification=verification,
                    source_count=len(sources),
                    reserved=reserved_names(workspace),
                )
                provenance = {
                    "kind": "playbook",
                    "distill_run_id": str(run_id),
                    "repo_key": repo_key,
                    "repo_label": repo_label,
                    "sources": [str(source.job_id) for source in sources],
                    "steps": [
                        {
                            "index": index,
                            "source": step.source,
                            "field": step.field,
                            "quote": step.quote,
                            "paths": list(step.paths),
                        }
                        for index, step in enumerate(verification.steps, start=1)
                    ],
                    "unverified_paths": list(verification.unverified_paths),
                }
                try:
                    version_id = ports.propose_version(
                        workspace=workspace,
                        text=text,
                        proposed_by=ports.ProposedBy.PLAYBOOK,
                        provenance=provenance,
                    )
                    outcome = Outcome.PROPOSED
                except ports.SkillProposalRefused as exc:
                    reason = f"refused:{exc.code}"
        run = _record(
            run_id,
            workspace=workspace,
            repo_key=repo_key,
            outcome=outcome,
            reason=reason,
            counts=counts,
            usage_id=usage_id,
        )
        if version_id is not None:
            run.version_id = version_id
            run.save(update_fields=["version", "updated_at"])
        PlaybookSource.objects.filter(pk__in=[source.pk for source in sources]).update(
            distill_run=run
        )
    return run


def distill_repo(*, workspace, repo_key, now=None):
    """One repo's distillation (see the module docstring). Returns the
    `PlaybookDistillRun`, or None when the repo has too few unused
    sources."""
    now = now or timezone.now()
    sources = list(unused_sources(workspace, repo_key)[: int(settings.SKILLS_PLAYBOOK_MAX_SOURCES)])
    if len(sources) < int(settings.SKILLS_PLAYBOOK_MIN_SOURCES):
        return None
    run_id = uuid.uuid4()
    common = {"workspace": workspace, "repo_key": repo_key}
    counts = {"sources": len(sources)}
    if proposals_this_week(workspace, now) >= int(settings.SKILLS_PLAYBOOK_WEEKLY_CAP):
        return _record(run_id, outcome=Outcome.SKIPPED_CAP, counts=counts, **common)
    if pending_playbook_proposals(workspace) >= int(settings.SKILLS_MAX_PENDING_PLAYBOOK):
        return _record(run_id, outcome=Outcome.SKIPPED_PENDING, counts=counts, **common)
    prompt_version = _prompt_version()
    if prompt_version is None:
        return _record(run_id, outcome=Outcome.NO_PROMPT, counts=counts, **common)
    recorder = DenialRecorder(workspace=workspace, source=RUN_SOURCE)
    usage_ids = []
    report = {"playbook": {"repo_key": repo_key, "sources": len(sources)}}
    run = None
    try:
        with unattended_run(str(run_id), allowed_purposes=ALLOWED_PURPOSES, on_denied=recorder):
            raw_text = None
            with unattended_step("distill"):
                try:
                    result = _call_model(
                        workspace=workspace,
                        prompt_version=prompt_version,
                        sources=sources,
                        report=report,
                        usage_ids=usage_ids,
                    )
                    raw_text = result.text
                    if not usage_ids and getattr(result.usage, "pk", None):
                        usage_ids.append(result.usage.pk)
                except BudgetExceeded:
                    run = _record(
                        run_id,
                        outcome=Outcome.BUDGET_REFUSED,
                        counts=counts,
                        usage_id=_first(usage_ids),
                        **common,
                    )
                except GatewayError as exc:
                    outcome = (
                        Outcome.NO_PROVIDER
                        if exc.error_type == "no_provider"
                        else Outcome.MODEL_ERROR
                    )
                    run = _record(
                        run_id,
                        outcome=outcome,
                        reason=str(exc.error_type or "")[:40],
                        counts=counts,
                        usage_id=_first(usage_ids),
                        **common,
                    )
            recorder.flush()
            if run is None:
                with unattended_step("propose"):
                    run = _propose(
                        run_id=run_id,
                        workspace=workspace,
                        repo_key=repo_key,
                        sources=sources,
                        raw_text=raw_text,
                        usage_id=_first(usage_ids),
                    )
                recorder.flush()
    except UnattendedDenied as exc:
        run = _record(
            run_id,
            outcome=Outcome.DENIED,
            reason=str(exc.effect)[:40],
            counts=counts,
            usage_id=_first(usage_ids),
            **common,
        )
    except Exception as exc:
        logger.warning("Playbook distillation failed (%s)", exc.__class__.__name__)
        if not PlaybookDistillRun.objects.filter(pk=run_id).exists():
            run = _record(
                run_id,
                outcome=Outcome.FAILED,
                reason=exc.__class__.__name__[:40],
                counts=counts,
                usage_id=_first(usage_ids),
                **common,
            )
    finally:
        recorder.flush()
    return run


def _first(values):
    return values[0] if values else None


def distill_playbooks(*, now=None):
    """The nightly entry point: every workspace with an eligible repo.
    One repo's failure never stops the others. Returns outcome counts."""
    now = now or timezone.now()
    totals = {}
    workspace_ids = (
        PlaybookSource.objects.filter(distill_run__isnull=True)
        .values_list("workspace_id", flat=True)
        .distinct()
    )
    for workspace in Workspace.objects.filter(pk__in=list(workspace_ids)).select_related("owner"):
        for repo_key in eligible_repos(workspace):
            try:
                run = distill_repo(workspace=workspace, repo_key=repo_key, now=now)
            except Exception as exc:
                logger.warning("Playbook distillation failed (%s)", exc.__class__.__name__)
                totals["failed"] = totals.get("failed", 0) + 1
                continue
            if run is not None:
                totals[run.outcome] = totals.get(run.outcome, 0) + 1
    return totals
