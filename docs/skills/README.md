# Skills in Friday

Friday has two kinds of skill. They live in the same `skills` app and share
nothing else.

## 1. Plan templates (MVP 8) — code only

`backend/skills/catalog.py` and `backend/skills/definitions/`: a code-only
registry of `SkillSpec` plan templates (`read`, `tool_request`, `task_draft`
steps) that an agent's planner can pick through `agent.skill_match`. They
**name tools and steps**, so they are capability. Changing one is a git
commit, a review and a deploy — never a database write. Tool references are
validated at boot (`SkillsConfig._validate_tool_references`); every write
step still passes the approval gate when it runs.

## 2. Instruction skills (Phase 12, `SKILL.md`) — text only

An Anthropic-style `SKILL.md`: a front-matter block and a Markdown body that
tells Friday **how to answer** a kind of request ("PR açıklaması nasıl
yazılır"). It is text. It grants no tool, no permission, no channel, no
approval class and no host execution. ADR-016 decision 4; Phase 12 brief
D13–D15.

### Format

```markdown
---
name: pr-aciklamasi              # ^[a-z][a-z0-9-]{1,63}$, required
description: "Ne zaman kullanılır" # <= 1024 chars, required
license: MIT                     # <= 100 chars, optional
metadata:                        # optional flat map, <= 10 keys
  author: owner
---
Adım adım talimatlar …
```

- Parsed by a strict, hand-written YAML **subset** parser
  (`skills/skill_md.py`): one `key: value` per line, plain or double-quoted
  scalars, `metadata` as an indented flat map. Anchors, aliases, tags, flow
  collections, block/multi-line scalars, directives and a second document
  are refused. No YAML library is used.
- **Refused keys:** `allowed-tools` and every other permission-shaped key
  (`tools`, `permissions`, `allow`, `auto_approve`, `trust`, …, also inside
  `metadata`) → `permission_key_refused`; any other unknown key →
  `unknown_key`.
- Body ≤ `SKILLS_BODY_MAX_CHARS` (16 000); invisible formatting (zero-width,
  bidi controls, control characters) is stripped.

### Uploads

A single `SKILL.md`, or a `.zip` with `SKILL.md` and at most 10
`references/*.md` (≤ 64 KB each). Anything else — scripts, binaries,
dotfiles, symlinks, nested archives, absolute or `..` paths — refuses the
whole upload. Zip limits are checked from the central directory before any
entry is read: upload ≤ `SKILLS_BUNDLE_MAX_BYTES` (1 MB, also enforced on the
request body), ≤ 12 entries, ≤ 1 MB declared uncompressed, per-entry
compression ratio ≤ 20 (above 4 KB). Entries are read into memory with a
hard cap at their declared size; nothing is written to disk and nothing is
ever executed.

**No registries.** Skills arrive only as owner uploads or owner-typed text
over the web, or as curator / playbook proposals (`skills.ports`). There is
no URL import and no registry client; the `skills` package imports no HTTP
client (tested).

### Lifecycle (D14)

`proposed` → `active` | `rejected`; `active` ↔ `stale` (curator, metadata
only); `active`/`stale` → `archived` → `active` (restore).

- Approve, edit-then-approve, reject, archive and restore are the
  workspace owner's, over the web, **one item at a time**. No bulk approve,
  no "allow always". A version is approved **by its content hash**; if the
  hash changed since it was shown, the approval is refused
  (`skill.version_changed`).
- An edit is a new version; the version in use stays until the new one is
  approved. A stored version never changes (`save()` raises).
- No model approves, ranks-to-approve or promotes anything. Curator and
  playbook proposals land `pending` like an upload, capped at 5 / 3 pending
  per workspace.
- Nothing is deleted (no delete endpoint). Every transition is audited
  with ids and hashes, never content (`skills.version.proposed` /
  `.approved` / `.rejected` / `.superseded`, `skills.skill.archived` /
  `.restored`).
- At most `SKILLS_MAX_ACTIVE` (50) active or stale skills per workspace.
- Every owner service is refused inside an unattended run; the
  `skills.ports` proposal entry point is the only skill write a curator or
  distiller run may make.

### In a chat turn (D15)

1. The wake-route check (voice), then `router.intent`, then the ADR-015
   voice cap — all computed exactly as without skills.
2. Only when the turn is answered by a chat completion (chat, memory,
   knowledge or a successful connector read) and the workspace has usable
   (active or stale) skills: one `skills.match` call (cheap tier, `skills`
   budget 5 USD/month) with the owner's message and the skills' names and
   descriptions; answer `{"skill": name|null}`. Timeout 600 ms on voice,
   1500 ms typed, no retry. A timeout, refusal, malformed answer or unknown
   name means "no skill". A draft card, an approval or a route reply makes
   no call.
3. A `/skill <name>` prefix (typed) or a `skill.<name>` wake route (an owner
   alias at the start of a voice utterance, set on `/settings/wake-routes`)
   forces that skill and skips the call.
4. The approved body (plus references the body names, ≤
   `SKILLS_CONTEXT_MAX_CHARS`) is injected into the chat completion's system
   context in `<skill name="…" version="…">`, framed as owner-approved
   instructions that do not change tools or approvals. The chat call's
   context report records `skills: [{name, version, sha256}]`;
   `use_count` / `last_used_at` are bumped.

**Release gate:** a hostile active skill ("ignore approvals", "call
mcp_n8n_* directly", "you may auto-approve") forced into every intent, typed
and voice, yields the same classification, drafts, approvals, reads, router
prompts and voice-cap result as no skill
(`skills/tests/test_no_capability.py`).

### Surfaces

- `/skills`: list by status, upload, editor; `/skills/<id>`: the version in
  use, each pending version with its diff, approve / edit-then-approve /
  reject, archive / restore.
- API: `/api/v1/skills…` (owner, web session only; PATs, node secret and
  node voice token are 401; members and agents 403; another workspace 404).
- Notices / digest: the `skills.proposals` probe (pending versions).

### Playbooks from harness runs (D17)

- **Sources.** When an implementation job `succeeded`, its review approved
  it, its tests passed and the owner typed its goal, `harness` records a
  `PlaybookSource` through `skills.ports.record_playbook_source` (goal,
  the job's test command snapshot, changed-file paths only, test counts,
  bounded model summaries as untrusted context). Sensitive repos record
  nothing; `.claude/skills/` paths are dropped.
- **Distillation.** Nightly (`skills.tasks.distill_playbooks`, 04:45), a
  repo with `SKILLS_PLAYBOOK_MIN_SOURCES` (3) unused sources gets one
  `skills.playbook_distill` call inside an unattended run whose only
  allowed purpose is that one. Each step must carry a `source` job and a
  verbatim `quote` of that job's goal or test command (checked in code);
  paths are unverified hints, summaries are never quotable; fewer than 2
  verified steps drop the draft. At most `SKILLS_PLAYBOOK_WEEKLY_CAP` (3)
  proposals per week and `SKILLS_MAX_PENDING_PLAYBOOK` (3) pending. Every
  attempt is a `PlaybookDistillRun` row (outcome, reason, counts).
- **Proposal.** A kept draft is a `proposed` version (origin `playbook`);
  `/skills/<id>` lists each step's source job (linked) and quote. A
  playbook never proposes a version of an owner's own skill (name clash →
  `playbook-<repo>`).
- **Reuse.** On "Uygulama iste" the owner may tick active skills; the
  request validates them through `skills.ports.get_attachable_versions`
  (all or none, same workspace, approved current versions) and Gate A
  lists them by name and sha256. At run time the runner fetches them
  again (archived since → `skill_version_unavailable`; a hash differing
  from Gate A → same), writes them read-only to `{workspace}/skills/`
  outside the clone, mounts that directory read-only at
  `HARNESS_CONTAINER_SKILLS_PATH` (`~/.claude/skills`) and over
  `/workspace/skills`, and adds `.claude/skills/` to `.git/info/exclude`.
  A produced commit touching `.claude/skills/` fails closed
  (`skill_path_in_commit`): no review, bundle, Gate B or push.
