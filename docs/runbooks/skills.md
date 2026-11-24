# Runbook — instruction skills, the curator and playbooks (Phase 12)

Instruction skills are `SKILL.md` text the owner approved, version by
version: Friday may read them while answering, and they grant nothing — no
tool, permission, channel, approval class or host execution (ADR-016
decision 4). The weekly curator and the nightly playbook distiller only
**propose**; the owner decides every version over the web. There is no
public registry, no URL import, no "allow always" and no model approval.

Sources: `docs/skills/README.md` (the format, parser refusals, upload
limits, lifecycle, matcher, curator and playbooks in detail),
`docs/prompts/phase-12-reach/brief.md` (D13–D18, R4, "Test strategy"
release gates), ADR-016 decisions 4–5, ADR-012 "Note 2026-11-20 — Phase 12
playbooks".

## What runs where

| Piece | Where | What it may do |
|---|---|---|
| `/skills`, `/skills/<id>` | the owner's browser (web channel, owner only) | upload / write / edit, approve **by hash**, reject, archive, restore; decide curator and playbook proposals |
| `skills.match` (purpose, tier `cheap`) | inside a chat turn | one bounded call (1.5 s; 600 ms on voice) choosing at most one active skill; never on a router-only path |
| `skills.tasks.curate` | worker beat, Sunday 04:15 | mark unused skills `stale` (> `SKILLS_STALE_DAYS`, 45), one `skills.curate` call, at most three proposals; runs unattended with only that purpose |
| `skills.tasks.distill_playbooks` | worker beat, nightly 04:45 | per repo with ≥ 3 new sources, one `skills.playbook_distill` call; every step's quote checked in code against owner/Friday-recorded fields |
| Harness implementation jobs | the runner | an approved skill version the owner attached is mounted read-only outside the clone; it never enters a commit |

## 1. Add a skill (owner step 5)

1. `/skills` → **Yükle** (a `SKILL.md` or a `.zip` with `references/*.md`)
   or write the text in the editor. The parser's refusal codes are shown on refusal
   (`permission_key_refused`, `unknown_key`, `body_too_large`,
   `host_execution_refused`, …; see `docs/skills/README.md`).
2. The version is `proposed`. Read it, then approve it — the approval names
   the content hash you were shown; a changed version answers
   `skill.version_changed`.
3. Ask something it covers in chat. The chat completion's context report
   lists `skills` (name, version, hash). `/skill <name>` at the start of a
   message, or a `skill.<name>` alias on `/settings/wake-routes`, picks it
   explicitly.

At most `SKILLS_MAX_ACTIVE` (50) skills are active. Skills are archived, never
deleted; archive and restore are on `/skills/<id>`.

## 2. Curator and playbook proposals

Both land as `pending` versions with provenance on `/skills` (the
`skills.proposals` probe puts "Beceri: …" in the digest and the bell). The
owner approves, edits-and-approves, or rejects each one; nothing is applied
automatically. Curator archive suggestions are shown with a reason and
dismissed or acted on by hand.

Caps: `SKILLS_MAX_PENDING_CURATOR` (5), `SKILLS_MAX_PENDING_PLAYBOOK` (3),
`SKILLS_PLAYBOOK_WEEKLY_CAP` (3). Playbook sources are recorded only for
succeeded, reviewed (approved verdict), tested jobs whose goal the owner
typed, never for a sensitive repo. A playbook step whose quote is not in
those fields is dropped; fewer than two verified steps drop the draft.

Read the runs:

```bash
# manage.py shell
from skills.models import SkillCuratorRun, PlaybookDistillRun
SkillCuratorRun.objects.order_by("-started_at").values("week", "outcome", "stale_marked",
    "proposals_created", "proposals_dropped", "drop_reasons", "denials")[:4]
PlaybookDistillRun.objects.order_by("-created_at").values("repo_key", "outcome", "reason",
    "counts")[:10]
```

## 3. Attaching a playbook to a harness job

On a new implementation request, pick approved skills (Gate A lists each by
name and hash). The runner mounts them read-only under
`HARNESS_CONTAINER_SKILLS_PATH`, outside the clone, and adds them to
`.git/info/exclude`; a version that is no longer attachable fails the job
closed (`skill_version_unavailable`), and a skill path in the commit fails it
(`skill_path_in_commit`). Whether the agent CLI actually picks up a mounted
skill is verified on the real runner (owner step, branch 7).

## 4. Preflight (`manage.py preflight`, `check_skills`)

| Row | Passes when | Fail / warn |
|---|---|---|
| `skills_ai` | `skills.match`, `skills.curate`, `skills.playbook_distill` each have a tier, an active budget and their seeded prompt | fail: any missing; warn: a purpose without a monthly cap |
| `skills_beat` | the weekly curator and nightly distiller are on the worker beat and registered | fail: an entry missing |
| `skills_parser` | every usable (active or stale) skill's current version passes **today's** parser | fail: `name=code` per refused skill — archive it or approve a corrected version |

`skills_parser` exists so that tightening the parser surfaces old approved
versions here before a turn meets them. Details carry purposes, budget
names, skill names and codes — never a body.

## 5. Release gates (CI)

- No capability from skills: `backend/skills/tests/test_no_capability.py`
  (a hostile active skill changes no decision, typed or voice).
- No outbound network from `skills`: `test_no_network.py`.
- Provenance: `test_playbook_provenance.py` (a step quoted from model output
  never survives).
- Unattended: the curator and distiller runs deny every D2 effect except
  their proposal write (`test_curator_unattended.py`,
  `test_playbook_unattended.py`).
- The closeout e2e `backend/ops/tests/test_reach_e2e.py`: upload → stays
  proposed (never offered) → approve → used in a turn; three fake harness
  jobs → one provenance-checked playbook → approved → attachable.

## Troubleshooting

- **A skill is never used.** It must be `active` (or `stale`), the
  `friday_skills_match` prompt seeded (`skills_ai`) and the matcher must
  answer within its timeout; a voice turn has a 600 ms budget.
- **`skill.version_changed` on approve.** Someone (or an edit) produced a new
  version; reload and approve what you see.
- **The curator proposed nothing.** `SkillCuratorRun.outcome` says why
  (`quiet`, `no_skills`, `skipped_cap`, `skipped_budget`, `no_prompt`,
  `no_provider`, `parse_error`, …).
