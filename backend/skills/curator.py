"""The weekly skill curator (Phase 12 D16).

A fixed system task (`skills.tasks.curate`, Sunday 04:15 `TIME_ZONE`, after
the 8B reflection), not an owner schedule. Per workspace with usable
(active or stale) skills, at most once per ISO week (`SkillCuratorRun`):

1. **Deterministic** (`mark_stale`, no model call): an active skill unused
   for `SKILLS_STALE_DAYS` (since its last use, or since its current version
   was approved when it has none) becomes `stale` -- metadata only, it is
   still offered to the matcher and shown dimmed; a stale skill used within
   the window is `active` again (`services.record_use` also flips it back
   at the moment of use).
2. **Proposals** (one gateway call, purpose `skills.curate`): given the
   usable skills' names, descriptions, bodies (owner-approved text) and
   usage, the model may propose at most `MAX_PROPOSALS` changes from a
   closed set -- `edit` (a new version of one skill), `merge` (two skills ->
   one new version of one of them, plus an archive suggestion for the
   other) and `archive` (a suggestion). The answer is strictly parsed; every
   proposed SKILL.md passes the same parser and refusals as an upload
   (`skills.ports.propose_version`); anything else is dropped and counted
   by a closed reason token.
3. Edit and merge proposals land as `pending` versions (`proposed_by =
   curator`); archive suggestions are stored on the run (closed reason
   tokens, no model text) and surfaced as notices through the
   `skills.proposals` probe. **Nothing is ever applied**: no version is
   approved, no skill archived, no owner decision taken.

The whole pass runs under `unattended_run(allowed_purposes={"skills.curate"})`
with an `audit.unattended.DenialRecorder`: it may write only skill
proposals, archive suggestions and stale flags (D2); every other effect
(an approval, task, memory, schedule, connector call, another purpose, an
owner skill decision) is refused by its owning service. The run row is
created before and finished after the context (bookkeeping outside it).

This module imports no HTTP client; the gateway is the single door
(ADR-003) and writes the one `ModelUsage` row.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ai_core.exceptions import BudgetExceeded, GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from audit.services import record_audit
from audit.unattended import DenialRecorder
from core import probes
from core.unattended import UnattendedDenied, unattended_run, unattended_step
from skills import ports, selectors, services
from skills.models import InstructionSkill, InstructionSkillVersion, SkillCuratorRun
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

CURATE_PURPOSE = "skills.curate"
CURATE_PROMPT_NAME = "friday_skills_curate"
AUDIT_SOURCE = "skills.curator"
MAX_PROPOSALS = 3

ACTION_EDIT = "edit"
ACTION_MERGE = "merge"
ACTION_ARCHIVE = "archive"
ACTIONS = frozenset({ACTION_EDIT, ACTION_MERGE, ACTION_ARCHIVE})
# What the model may give as an archive reason; `merged` is set by code only.
MODEL_ARCHIVE_REASONS = frozenset({"unused", "duplicate", "outdated"})
ARCHIVE_REASONS = MODEL_ARCHIVE_REASONS | {"merged"}

_ITEM_KEYS = {
    ACTION_EDIT: frozenset({"action", "skill", "skill_md"}),
    ACTION_MERGE: frozenset({"action", "skills", "skill_md"}),
    ACTION_ARCHIVE: frozenset({"action", "skill", "reason"}),
}

Status = InstructionSkill.Status
Outcome = SkillCuratorRun.Outcome
ProposedBy = InstructionSkillVersion.ProposedBy
Decision = InstructionSkillVersion.Decision


class CuratorParseError(ValueError):
    pass


def iso_week(now) -> str:
    year, week, _ = timezone.localtime(now).isocalendar()
    return f"{year}-W{week:02d}"


# --- 1. the deterministic pass ----------------------------------------------


def _reference_time(skill):
    """When the skill last counted as used: its last use, or the approval of
    the version in use (a new version restarts the clock)."""
    candidates = [skill.last_used_at]
    if skill.current_version is not None:
        candidates.append(skill.current_version.decided_at)
    candidates = [value for value in candidates if value is not None]
    return max(candidates) if candidates else skill.created_at


def _audit_status(action, skill, *, previous, reference):
    record_audit(
        action=action,
        actor=None,
        actor_type="system",
        workspace=skill.workspace,
        resource=skill,
        metadata={
            "skill_id": str(skill.pk),
            "previous": previous,
            "version_id": str(skill.current_version_id or ""),
            "reference": reference.isoformat() if reference else "",
            "source": AUDIT_SOURCE,
        },
    )


def mark_stale(workspace, *, now=None):
    """Active skills unused for `SKILLS_STALE_DAYS` -> `stale`; stale skills
    used inside that window -> `active`. No model call. Returns
    `(stale_marked, reactivated)`."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=int(settings.SKILLS_STALE_DAYS))
    stale_marked = reactivated = 0
    with transaction.atomic():
        services.lock_workspace(workspace)
        rows = (
            InstructionSkill.objects.select_for_update(of=("self",))
            .select_related("current_version", "workspace")
            .filter(
                workspace=workspace,
                status__in=[Status.ACTIVE, Status.STALE],
                current_version__isnull=False,
            )
            .order_by("name")
        )
        for skill in rows:
            reference = _reference_time(skill)
            if skill.status == Status.ACTIVE and reference < cutoff:
                skill.status = Status.STALE
                skill.save(update_fields=["status", "updated_at"])
                _audit_status("skills.skill.stale", skill, previous="active", reference=reference)
                stale_marked += 1
            elif skill.status == Status.STALE and reference >= cutoff and skill.last_used_at:
                skill.status = Status.ACTIVE
                skill.save(update_fields=["status", "updated_at"])
                _audit_status(
                    "skills.skill.reactivated", skill, previous="stale", reference=reference
                )
                reactivated += 1
    return stale_marked, reactivated


# --- 2. the proposal call ----------------------------------------------------


def parse_proposals(raw_text):
    """`{"proposals": [<item>, ...]}` and nothing else. Returns the list of
    raw items; each item is validated on its own (`_apply_item`) so one bad
    item drops only itself."""
    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError) as exc:
        raise CuratorParseError("invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"proposals"}:
        raise CuratorParseError("unexpected shape")
    items = payload["proposals"]
    if not isinstance(items, list):
        raise CuratorParseError("proposals must be a list")
    return items


@dataclass
class _Candidate:
    skill: InstructionSkill
    complete: bool  # the whole body was in the model's input


@dataclass
class _Tally:
    created: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    drops: dict = field(default_factory=dict)
    usage_id: object = None
    considered: int = 0

    def drop(self, reason):
        self.drops[reason] = self.drops.get(reason, 0) + 1


def _prompt_version():
    template = (
        PromptTemplate.objects.filter(name=CURATE_PROMPT_NAME)
        .select_related("active_version")
        .first()
    )
    if template is None or template.active_version_id is None:
        return None
    return template.active_version


def build_input(skills, *, now):
    """(block text, {name: _Candidate}). Bodies are cut to
    `SKILLS_CURATE_BODY_MAX_CHARS`; once the block would pass
    `SKILLS_CURATE_INPUT_MAX_CHARS`, further skills are listed without a
    body. Only a skill whose whole body was shown may be edited or merged."""
    body_max = int(settings.SKILLS_CURATE_BODY_MAX_CHARS)
    budget = int(settings.SKILLS_CURATE_INPUT_MAX_CHARS)
    rows, candidates, used = [], {}, 0
    for skill in skills:
        version = skill.current_version
        last = skill.last_used_at
        row = {
            "name": skill.name,
            "status": skill.status,
            "use_count": int(skill.use_count),
            "days_since_use": (now - last).days if last else None,
            "description": version.description,
        }
        body = version.body
        complete = len(body) <= body_max
        shown = body if complete else body[:body_max]
        cost = len(json.dumps(row, ensure_ascii=False)) + len(shown) + 40
        if used + cost > budget:
            row["body"] = None
            complete = False
        else:
            row["body"] = shown
            used += cost
        row["body_complete"] = complete
        rows.append(row)
        candidates[skill.name] = _Candidate(skill=skill, complete=complete)
    text = json.dumps(rows, ensure_ascii=False).replace("</skills", "<\\/skills")
    return "\n\n<skills>\n" + text + "\n</skills>", candidates


def _pending_other_proposer(skill):
    return (
        InstructionSkillVersion.objects.filter(skill=skill, decision=Decision.PENDING)
        .exclude(proposed_by=ProposedBy.CURATOR)
        .exists()
    )


def _version_ref(skill):
    version = skill.current_version
    return {
        "skill_id": str(skill.pk),
        "version_id": str(version.pk),
        "sha256": version.content_sha256,
    }


def _propose(run, workspace, text, *, provenance, tally):
    try:
        version_id = ports.propose_version(
            workspace=workspace,
            text=text,
            proposed_by=ProposedBy.CURATOR,
            provenance=provenance,
        )
    except ports.SkillProposalRefused as exc:
        tally.drop(exc.code)
        return None
    tally.created.append(str(version_id))
    return version_id


def _suggest(tally, skill, reason, **extra):
    if any(s["skill_id"] == str(skill.pk) for s in tally.suggestions):
        return False
    tally.suggestions.append(
        {"skill_id": str(skill.pk), "reason": reason, "dismissed": False, **extra}
    )
    return True


def _parsed_name(text, tally):
    if not isinstance(text, str) or not text.strip():
        tally.drop("invalid_item")
        return None
    try:
        return services.parse_text(text).name
    except services.SkillServiceError as exc:
        tally.drop(exc.reason or "invalid")
        return None


def _apply_item(item, *, run, workspace, candidates, touched, tally):
    if not isinstance(item, dict):
        tally.drop("invalid_item")
        return
    action = item.get("action")
    if action not in ACTIONS:
        tally.drop("unknown_action")
        return
    if set(item) != _ITEM_KEYS[action]:
        tally.drop("invalid_item")
        return
    base = {"curator_run_id": str(run.pk), "week": run.week, "action": action}

    if action == ACTION_ARCHIVE:
        name, reason = item["skill"], item["reason"]
        if not isinstance(name, str) or name not in candidates:
            tally.drop("unknown_skill")
            return
        if reason not in MODEL_ARCHIVE_REASONS:
            tally.drop("invalid_reason")
            return
        if not _suggest(tally, candidates[name].skill, reason):
            tally.drop("duplicate_target")
        return

    if action == ACTION_EDIT:
        name = item["skill"]
        if not isinstance(name, str) or name not in candidates:
            tally.drop("unknown_skill")
            return
        targets = [name]
    else:
        names = item["skills"]
        if (
            not isinstance(names, list)
            or len(names) != 2
            or not all(isinstance(n, str) for n in names)
            or names[0] == names[1]
        ):
            tally.drop("invalid_item")
            return
        if not all(n in candidates for n in names):
            tally.drop("unknown_skill")
            return
        targets = list(names)

    parsed_name = _parsed_name(item["skill_md"], tally)
    if parsed_name is None:
        return
    if parsed_name not in targets:
        tally.drop("name_mismatch")
        return
    if any(n in touched for n in targets):
        tally.drop("duplicate_target")
        return
    if not all(candidates[n].complete for n in targets):
        tally.drop("incomplete_input")
        return
    target = candidates[parsed_name].skill
    if _pending_other_proposer(target):
        # Never supersede the owner's (or a playbook's) pending version.
        tally.drop("other_pending")
        return
    provenance = {**base, "base": _version_ref(target)}
    if action == ACTION_MERGE:
        provenance["merged_from"] = [_version_ref(candidates[n].skill) for n in targets]
    version_id = _propose(run, workspace, item["skill_md"], provenance=provenance, tally=tally)
    if version_id is None:
        return
    touched.update(targets)
    if action == ACTION_MERGE:
        other = next(n for n in targets if n != parsed_name)
        _suggest(
            tally,
            candidates[other].skill,
            "merged",
            into_skill_id=str(target.pk),
            version_id=str(version_id),
        )


def _pending_curator_count(workspace):
    return InstructionSkillVersion.objects.filter(
        workspace=workspace, decision=Decision.PENDING, proposed_by=ProposedBy.CURATOR
    ).count()


def propose_changes(run, workspace, *, now, tally):
    """The one `skills.curate` call and its strictly checked application.
    Returns the outcome token (`Outcome`)."""
    owner = workspace.owner
    skills = selectors.usable_skills(owner, workspace)
    tally.considered = len(skills)
    if not skills:
        return Outcome.NO_SKILLS
    if _pending_curator_count(workspace) >= int(settings.SKILLS_MAX_PENDING_CURATOR):
        return Outcome.SKIPPED_CAP
    prompt = _prompt_version()
    if prompt is None:
        return Outcome.NO_PROMPT
    personal_data = probes.data_class_of(workspace) == probes.DATA_CLASS_PERSONAL
    gateway = InferenceGateway()
    if not gateway.has_eligible_provider(purpose=CURATE_PURPOSE, personal_data=personal_data):
        return Outcome.NO_PROVIDER
    block, candidates = build_input(skills, now=now)
    usage_ids = []
    report = {
        "skills_curator": {
            "run_id": str(run.pk),
            "week": run.week,
            "skills": len(skills),
            "bodies_incomplete": sum(1 for c in candidates.values() if not c.complete),
        }
    }
    messages = [
        {"role": "system", "content": prompt.content + block},
        {"role": "user", "content": "Review the skills and answer with the JSON object."},
    ]
    try:
        result = gateway.complete(
            CURATE_PURPOSE,
            messages,
            actor=owner,
            workspace=workspace,
            prompt_version=prompt,
            personal_data=personal_data,
            context_report=report,
            on_usage=lambda usage: usage_ids.append(usage.pk),
        )
    except BudgetExceeded:
        tally.usage_id = usage_ids[0] if usage_ids else None
        return Outcome.SKIPPED_BUDGET
    except GatewayError as exc:
        tally.usage_id = usage_ids[0] if usage_ids else None
        if getattr(exc, "error_type", "") == "no_provider":
            return Outcome.NO_PROVIDER
        return Outcome.GATEWAY_ERROR
    tally.usage_id = (usage_ids[0] if usage_ids else None) or getattr(result.usage, "pk", None)
    try:
        items = parse_proposals(result.text)
    except CuratorParseError:
        return Outcome.PARSE_ERROR
    touched = set()
    for index, item in enumerate(items):
        if index >= MAX_PROPOSALS:
            tally.drop("over_limit")
            continue
        _apply_item(
            item,
            run=run,
            workspace=workspace,
            candidates=candidates,
            touched=touched,
            tally=tally,
        )
    return Outcome.PROPOSALS if (tally.created or tally.suggestions) else Outcome.QUIET


# --- the run -----------------------------------------------------------------


def curate_workspace(workspace, *, now=None):
    """One curator pass over one workspace for the ISO week of `now`.
    Returns the `SkillCuratorRun`, or None when this week already ran."""
    now = now or timezone.now()
    week = iso_week(now)
    try:
        with transaction.atomic():
            run, created = SkillCuratorRun.objects.get_or_create(
                workspace=workspace, week=week, defaults={"started_at": now}
            )
    except IntegrityError:
        return None
    if not created:
        return None
    recorder = DenialRecorder(workspace=workspace, source=AUDIT_SOURCE)
    tally = _Tally()
    stale_marked = reactivated = 0
    outcome = Outcome.FAILED
    denials = 0
    try:
        with unattended_run(
            str(run.pk), allowed_purposes=frozenset({CURATE_PURPOSE}), on_denied=recorder
        ) as context:
            try:
                with unattended_step("stale"):
                    stale_marked, reactivated = mark_stale(workspace, now=now)
                recorder.flush()
                with unattended_step("curate"):
                    outcome = propose_changes(run, workspace, now=now, tally=tally)
            finally:
                denials = len(context.denials)
                recorder.flush()
    except UnattendedDenied:
        outcome = Outcome.DENIED
    except Exception as exc:
        logger.warning("Skill curator run %s failed (%s)", run.pk, exc.__class__.__name__)
        outcome = Outcome.FAILED
    finally:
        recorder.flush()
    SkillCuratorRun.objects.filter(pk=run.pk).update(
        outcome=outcome,
        finished_at=timezone.now(),
        stale_marked=stale_marked,
        reactivated=reactivated,
        skills_considered=tally.considered,
        proposals_created=len(tally.created),
        proposals_dropped=sum(tally.drops.values()),
        drop_reasons=tally.drops,
        proposal_version_ids=tally.created,
        archive_suggestions=tally.suggestions,
        denials=denials,
        model_usage_id=tally.usage_id,
    )
    run.refresh_from_db()
    return run


def curate_all(*, now=None):
    """Every workspace with usable skills; never raises for one workspace."""

    now = now or timezone.now()
    workspace_ids = (
        InstructionSkill.objects.filter(
            status__in=selectors.USABLE_STATUSES, current_version__isnull=False
        )
        .values_list("workspace_id", flat=True)
        .distinct()
    )
    results = []
    for workspace in Workspace.objects.filter(pk__in=list(workspace_ids)).order_by("created_at"):
        try:
            run = curate_workspace(workspace, now=now)
        except Exception:
            logger.exception("Skill curator failed for workspace %s", workspace.pk)
            results.append({"workspace": str(workspace.pk), "outcome": "unexpected"})
            continue
        results.append(
            {
                "workspace": str(workspace.pk),
                "outcome": run.outcome if run is not None else "already_ran",
            }
        )
    return results
