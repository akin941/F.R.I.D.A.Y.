# MVP 8 — Skills: Reusable Plan Templates

> Status: **plan approved (2026-07-13)** — 6 rounds of Codex plan-review
> (round 1: 1 HIGH + 4 MEDIUM + 2 LOW; round 2: 1 MEDIUM + 2 LOW; round 3:
> 1 MEDIUM; round 4: 2 MEDIUM; round 5: 1 MEDIUM + 1 LOW; round 6: 0
> findings, APPROVE — Codex additionally ran an executable Python check
> confirming the `SkillSpec`/`SkillStep` dataclass and hash logic behaves
> as described). Scope locked with the user (2026-07-13) after an explicit
> analysis-before-brief pass — see the framework below, which is the
> user's own locked decision, not a proposal open for silent
> renegotiation. The round-1 HIGH (a claimed reuse of `agents.planner`'s
> validators that contradicted `skills` never importing `agents`) was
> escalated to the user, who chose duplicating a local validator with
> parity tests over the alternatives (sharing via `core`, or loosening the
> import-linter direction) — see §1. All MEDIUM/LOW findings (mostly
> implementation-validity bugs in the `SkillSpec.content_hash`/
> `SkillStep.payload` immutability mechanics, and a `ModelUsage`
> success/failure semantics mismatch for the new `agent.skill_match` call)
> were closed autonomously per standing protocol.
>
> **Status: MVP 8 COMPLETE (2026-07-13)** — all 3 branches merged to main
> (`794572f`, `a3bae79`, `80b41fe`). See the Branch plan table below for
> per-branch Codex code-review round counts and findings.

## Goal and user value

MVP 7 gave Jarvis the ability to turn a goal into a plan, via one
`purpose="agent.plan"` gateway call per agent, every time. For goals that
recur in a predictable shape ("export my weekly notes," "turn this note into
a task"), that call is pure waste: the same LLM, given essentially the same
goal, re-derives essentially the same plan on every run, at the cost of a
gateway call and the small but real risk of variance (a slightly different
plan each time for what is conceptually the same, already-proven-safe
request).

A **Skill** is a git-authored, pre-approved plan template — the answer to
"we already know how to do this; skip re-planning it from scratch." This MVP
adds a mechanism to register such templates and a narrow, safe way for the
agent runtime to recognize when a goal matches one, without touching the
execution engine, the approval gate, or any of MVP 7's brakes at all.

## Relationship to Agents (MVP 7) — the boundary this MVP draws

This is not a new execution primitive. It is a **shortcut into the existing
one**:

- **Agents (MVP 7, shipped)**: goal → one `agent.plan` gateway call →
  freeform `{"steps": [...]}` → `AgentStep` rows (`read` / `tool_request` /
  `task_draft`) → the existing engine executes them one at a time, gated
  identically regardless of who or what proposed the step.
- **Skills (this MVP)**: goal → an additional, narrower gateway call that
  asks "does a known, git-authored template already cover this goal?" →
  if yes, the template's own fixed `{"steps": [...]}` is used **in place
  of** the `agent.plan` call (which is skipped entirely) → the exact same
  `AgentStep` rows, the exact same engine, the exact same approval gate.

Nothing about how a step *executes* changes. A `tool_request` step
originating from a skill template pauses at `blocked_on_approval` and goes
through `approvals.services.create_request` exactly as an LLM-freeform step
already does (`agents/execution.py:187-189`) — a skill can propose steps,
never approve or execute them. This is deliberate: reusing the execution
engine means MVP 7's seven-review-round hardening (timeout, consecutive-
failure halt, gateway-call budget, kill switch, recovery scan) applies to
skill-originated plans for free, with zero new execution-time risk surface.

## Scope

### 1. `skills` app foundation — code-only registry, no DB table

New Django kernel app `skills`, siblings with `agents` (ADR-002 already
names `agents, skills` at the same layer). Mirrors `tools/registry.py`'s
existing pattern deliberately, not `ai_core.PromptTemplate`/`PromptVersion`'s:
the latter is "immutable-by-discipline" but not code-enforced (no override
prevents editing an existing `PromptVersion` row) — a plain, code-only
registry gives a *stronger* self-modification guarantee, since there is no
runtime data structure for an agent to mutate at all. Changing a skill
requires a git commit + PR review + deploy, never a database write.

`skills/catalog.py`:

```python
@dataclass(frozen=True)
class SkillStep:
    kind: str            # "read" | "tool_request" | "task_draft" — same closed
                          # set agents.models.AgentStep.Kind already defines
    description: str
    payload: dict         # same per-kind shape agents.planner.parse_plan()
                          # already validates (empty for read, {"tool_name":
                          # ...} for tool_request, {"title", "due_date"} for
                          # task_draft)

    def __post_init__(self):
        # Defensive: a plain dict is mutable even on a frozen dataclass
        # (frozen only blocks reassigning the *attribute*, not mutating the
        # dict it points to) — wrap it read-only so nothing can mutate a
        # step's payload after construction and silently invalidate the
        # SkillSpec-level content_hash (below) that was computed from it.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str      # shown to the skill-match prompt, mirrors
                          # agents.planner_service._TOOL_DESCRIPTIONS' role
    trigger_hint: str      # short example phrase(s), shown to the same prompt
    steps: tuple[SkillStep, ...]
    content_hash: str = field(init=False, default="")
                          # NEVER supplied by a skill's own definition module —
                          # a skill author writes SkillSpec(name=...,
                          # description=..., trigger_hint=..., steps=(...))
                          # with no content_hash argument at all (see the two
                          # example skills, §7); __post_init__ below computes
                          # and fills it in before the object is ever handed
                          # to register_skill().

    def __post_init__(self):
        # Same class of bug as SkillStep.payload above: `steps` is only
        # annotated `tuple[...]`, which Python does not enforce -- a caller
        # passing a list would let later external mutation silently change
        # the step template without changing content_hash. Normalize before
        # hashing, and store the normalized tuple back onto the instance.
        steps = tuple(self.steps)
        object.__setattr__(self, "steps", steps)
        computed = hashlib.sha256(
            json.dumps(
                {
                    "name": self.name,
                    "description": self.description,
                    "trigger_hint": self.trigger_hint,
                    "steps": [
                        {"kind": s.kind, "description": s.description,
                         "payload": dict(s.payload)}
                        for s in steps
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        # Standard pattern for a derived field on a frozen dataclass — NOT
        # dataclasses.replace(), which cannot target an init=False field.
        object.__setattr__(self, "content_hash", computed)

_REGISTRY: dict[str, SkillSpec] = {}

def register_skill(spec: SkillSpec): ...   # stores spec (content_hash already
                                            # set by __post_init__) directly
                                            # in _REGISTRY, no further mutation
def unregister_skill(name: str): ...   # test-only, mirrors tools.registry's
def get_skill(name: str) -> SkillSpec | None: ...
def list_skill_names() -> list[str]: ...
def list_skill_prompt_entries() -> list[tuple[str, str, str]]:
    # (name, description, trigger_hint) per registered skill, for building
    # the skill-match prompt's vocabulary — mirrors list_tool_names()'s role
```

`SkillStep.kind`/`payload` are validated by `skills.catalog`'s **own**
per-kind shape checks at registration time — a small, self-contained
duplicate of `agents.planner._parse_step`'s validation logic (read payload
must be `{}`; `tool_request` payload keys must be exactly `{"tool_name"}`;
`task_draft` payload keys must be exactly `{"title", "due_date"}` with the
same `YYYY-MM-DD`-or-`null` rule), not an import of it. **Deliberately
duplicated, not shared**, because `skills` must not import `agents`
(`agents.planner` lives inside the `agents` app) — this is the same
established tradeoff already used elsewhere in this codebase for the same
one-way-dependency reason: `agents/planner_service.py`'s own
`_TOOL_DESCRIPTIONS` duplicates rather than imports `assistant`'s tool
vocabulary, and `ai_core`'s `FakeAdapter` fixtures duplicate rather than
import every business app's schema they mirror. A skill step still fails
at Django startup with the same shape errors a malformed LLM plan would
raise, never a silently-wrong template reaching an `Agent` — this branch's
tests include parity cases proving `skills.catalog`'s validator and
`agents.planner`'s validator accept and reject the exact same set of
shapes, so the two implementations can't silently drift apart.

Two example skills ship in this branch (§7), each its own module under
`skills/definitions/`, registered from `SkillsConfig.ready()` exactly the
way `tools/apps.py` registers each tool module today.

**Import-linter**: add `"skills"` to `root_packages`. New contracts:
`skills` must not import `agents` (one-way: `agents -> skills`, mirroring
the existing `tools`/`approvals`/`tasks`/`memory`/`knowledge` shape);
`skills` may import `tools` (needed for §2's boot-time validation) but
`tools` must not import `skills` (mirrors the existing `approvals -> tools`
direction, where `approvals` depends on `tools` and not the reverse);
`ai_core` must not import `skills` (add to the existing business-app
forbidden list, same reasoning as every other business app).

### 2. Boot-time tool-reference validation (fail loud at startup, not at run)

`SkillsConfig.ready()`: for every registered `SkillSpec`, for every
`tool_request`-kind `SkillStep`, assert `payload["tool_name"]` is a member
of `tools.registry.list_tool_names()` **at Django startup** — raise
`ImproperlyConfigured` immediately if not, mirroring
`ApprovalsConfig.ready()`'s existing `DEV_ALLOW_SELF_APPROVAL` fail-fast
convention (`approvals/apps.py`) rather than letting a dangling tool
reference surface only when some future agent happens to run that skill.

**Ordering constraint**: `SkillsConfig.ready()` must run after
`ToolsConfig.ready()` has registered every tool module (Django calls
`ready()` in `INSTALLED_APPS` order) — `skills` must be listed after
`tools` in `INSTALLED_APPS`. This is the same category of ordering
sensitivity `ApprovalsConfig` already has; call it out explicitly in code
comments so a future re-ordering of `INSTALLED_APPS` doesn't silently
reintroduce a boot-time false negative (skills validated before tools
exist, meaning nothing fails even though a reference actually dangles).

Rationale (why this matters, restated from the user's framing): a tool can
be renamed, removed, or have its expected payload shape changed by a later
MVP without anyone remembering a skill still names it — without this check,
that skill breaks silently and only surfaces when an agent actually tries
to run it, at which point the failure is buried inside a running agent's
step instead of at deploy time.

### 3. Skill selection — `purpose="agent.skill_match"`, before `agent.plan`

A new, narrower gateway call precedes today's `agent.plan` call in
`agents/planner_service.py::create_agent_from_goal()`. This is an
**additive, skippable optimization** — its failure mode in every case is
"fall through to the exact `agent.plan` behavior this MVP found already
working," never a new way for agent creation to fail.

**Prompt** (new `purpose="agent.skill_match"`, `PromptTemplate.name =
"jarvis_agent_skill_match"` — following the same name/purpose pairing
`jarvis_agent_plan`/`purpose="agent.plan"` already establishes — seeded via
`seed_ai_defaults.py` following the existing hand-typed-constant
convention): given the goal and the registered skill list (name +
description + trigger_hint, from `list_skill_prompt_entries()`), return
strict JSON `{"skill_name": "<name>" | null}` — no other shape. The prompt
text explicitly instructs: *only return a skill_name if the goal is an
unambiguous, essentially-exact match for that skill's stated purpose; if
there is any doubt, partial fit, or ambiguity, return null.*

**Why binary null/non-null instead of a self-reported numeric or
categorical confidence score** (this directly answers the user's
requirement #3, "zayıf eşleşme → serbest planlama"): LLM self-reported
confidence values are themselves an unreliable, hallucination-prone signal
— asking the model to grade its own certainty adds a second unvalidatable
value rather than removing risk. Instead, the *prompt itself* encodes the
threshold ("only if unambiguous"), and the *system* treats non-null as an
unconditional trigger with its own independent validation (below) — the
model's only real decision is binary (name a skill, or don't), which is
both easier to get right and easier to test deterministically (a
`FakeAdapter` content-trigger fixture, mirroring `router.intent`'s existing
convention, returns a non-null `skill_name` only for a specific trigger
phrase and `null` for everything else).

**Validation, independent of the prompt's own honesty**: even a non-null
response is checked against `skills.catalog.list_skill_names()` before
being trusted — a hallucinated or since-removed skill name degrades to
`agent.plan` exactly like `null` would, never raises, never fails the
agent. This mirrors the existing `tool_name`-against-registry validation
`create_agent_from_goal` already does for `tool_request` steps
(`agents/planner_service.py:115-120`).

**Flow** (`create_agent_from_goal`, extended):

1. Resolve the `agent.plan` prompt version before creating the `Agent`
   row — **unchanged from today** (`agents/planner_service.py:82-90`).
   The `agent.skill_match` prompt version is deliberately **not**
   resolved here: this call is an optional, skippable optimization
   layered in front of `agent.plan`, never a second hard prerequisite for
   agent creation. Requiring it upfront would turn a missing/inactive
   `agent.skill_match` config into a new way for agent creation to fail
   even when the pre-existing `agent.plan` path is perfectly healthy —
   exactly the regression this design must not introduce.
2. Create the `Agent` row (`planning` status), as today.
3. Attempt to resolve the `agent.skill_match` prompt version and call it.
   **Any** outcome other than "a valid, registry-resolvable skill name"
   falls through to step 5, but the three ways that can happen are not
   identical underneath and matter for what gets recorded, mirroring the
   existing `InferenceGateway`/`agent.plan` contract exactly (no new
   accounting rule invented for this call):
   - **Missing/inactive prompt config**: resolution fails *before* any
     gateway call is attempted — no `agent.skill_match` `ModelUsage` row
     is written at all (there is nothing to record; the gateway is
     gateway-owned accounting, per ADR-003, and this path never reaches
     it), exactly like `agent.plan` behaves for the same config-missing
     case today.
   - **`GatewayError`**: the adapter call itself failed — a
     `ModelUsage` row is written with `success=False`.
   - **Parse failure** (malformed JSON, wrong shape): the gateway call
     itself *succeeded* (the adapter returned a response); only this
     code's own downstream schema check rejected it — the `ModelUsage`
     row is written with `success=True`, identical to how `agent.plan`'s
     own existing parse-failure tests already assert
     (`agents/tests/test_planner_service.py`'s
     `test_malformed_planner_output_fails_agent_with_planner_error`
     pattern) — `InferenceGateway` records success as soon as the adapter
     returns, before any downstream JSON parsing (`ai_core/gateway.py`).

   Whichever of the three applies, the agent itself never fails because
   of it — only step 5's fallback triggers. This call does **not** touch
   `Agent.gateway_calls_used` — that counter is a dedicated execution-time
   brake introduced in MVP 7 branch 3 (checked in `_claim_next_step`
   before allowing a further `read` step, incremented only by
   `_finalize_read_step`'s embed+summarize pair); `agent.plan` itself is
   not counted there either, and `agent.skill_match` is the same kind of
   planning-phase call, out of that counter's scope by the same existing
   design, not a new exception to it.
4. If the response names a skill and that name resolves via
   `get_skill()`: convert `SkillSpec.steps` to the same dict shape
   `parse_plan()` already produces and call `finalize_plan()` **passing
   `skill_name`/`skill_content_hash` through to it** (see below) —
   **skipping the `agent.plan` gateway call entirely.** This is the
   actual efficiency gain: one fewer LLM call for a matched goal, not
   just a faster plan.
5. Otherwise (null, hallucinated name, parse failure, or gateway error):
   proceed to the existing `agent.plan` gateway call exactly as it works
   today, unmodified, and call `finalize_plan()` with no skill provenance
   — `Agent.skill_name`/`skill_content_hash` stay empty.

**`finalize_plan()` gains two optional keyword arguments,
`skill_name=""`/`skill_content_hash=""`, but no new behavior otherwise.**
The plan-step-count bound (`AGENT_MAX_PLAN_STEPS`) and the empty-plan-fails
check (MVP 7 §2) apply to a skill's step list exactly as they apply to a
freeform LLM plan, unmodified — a skill authored with more steps than the
bound still fails clean with `plan_too_large`, never silently bypassing
that brake. The two new fields are set in the **same
`select_for_update()`-locked block** that already checks for
cancellation-during-planning and creates the `AgentStep` rows
(`agents/services.py:120-138`) — not before calling `finalize_plan()`, and
not in a separate save. This closes a real race the brief's first draft
missed: setting the audit fields *before* the lock could annotate a
cancelled-during-planning `Agent` (which `finalize_plan()` correctly
no-ops on) as having used a skill it never actually ran.

### 4. Approval-gate invariant — no new mechanism, but stated and tested

A skill's step template can **propose** `kind`/`description`/`payload` —
nothing else. It cannot mark a step pre-approved, cannot set
`AgentStep.status` past `pending` at creation, and cannot bypass
`_dispatch_tool_request_step`'s call to
`approvals.services.create_request`. This is already true by construction
(skill-templated steps become ordinary `AgentStep` rows via the same
`finalize_plan()` every freeform plan uses), but it needs an explicit test,
not just an architectural argument: a test proving a skill-matched agent's
`tool_request` step reaches `blocked_on_approval` and requires the same
`requester != decider` permission check as a freeform-planned one — the
existing permission test (MVP 7 §6, "the test that must never regress")
re-run against a skill-originated plan instead of an LLM-freeform one,
proving the invariant holds regardless of plan origin.

### 5. Self-modification guarantee extension

One new static test, alongside `agents/tests/test_self_modification.py`'s
existing `TestNoRegistryOrPermissionBypass` class: the execution engine and
`planner_service` never call `skills.catalog.register_skill`/
`unregister_skill` — the direct twin of the existing
`test_engine_never_calls_tools_registry_mutation_functions`. Agents may
only ever **read** the skill catalog (`list_skill_prompt_entries`,
`get_skill`) to pick a template; they can never register, remove, or
modify one.

### 6. Audit trail — `Agent.skill_name` / `Agent.skill_content_hash`

Two new nullable fields on `Agent` (migration, both blank-default, no
backfill needed — existing agents simply read as "no skill used"):

- `skill_name` (CharField, blank/default `""`): the matched skill's name,
  set only in flow step 4 above.
- `skill_content_hash` (CharField, blank/default `""`): the `SkillSpec`'s
  `content_hash` **at the moment the agent used it**, copied onto the
  `Agent` row at plan-creation time — not looked up from the live catalog
  later. This directly mirrors a common
  `prompt_key + prompt_version` usage-record pattern: months
  later, "why did this agent behave this way" is answerable from the
  `Agent` row itself even if the skill's template has since changed in a
  later deploy (a new git commit changes `content_hash`, but old `Agent`
  rows still point at the hash that was actually live when they ran).

### 7. Two example skills — zero new primitives

Both use only the three existing `AgentStep` kinds; neither needs a new
tool, a new step kind, or any execution-engine change.

`skills/definitions/weekly_export.py`:
```python
SkillSpec(
    name="weekly_export",
    description="Summarize recent notes and export them to the Obsidian vault.",
    trigger_hint="e.g. 'export my weekly notes', 'haftalık notlarımı vault'a aktar'",
    steps=(
        SkillStep(kind="read", description="Summarize recent notes", payload={}),
        SkillStep(kind="tool_request", description="Export the vault",
                  payload={"tool_name": "vault_export"}),
    ),
)
```

`skills/definitions/note_to_task.py`:
```python
SkillSpec(
    name="note_to_task",
    description="Turn a referenced note into a follow-up task.",
    trigger_hint="e.g. 'turn this note into a task', 'bu notu görev haline getir'",
    steps=(
        SkillStep(kind="read", description="Retrieve and summarize the referenced note",
                  payload={}),
        SkillStep(kind="task_draft", description="Draft a follow-up task from the summary",
                  payload={"title": "Follow up on note", "due_date": None}),
    ),
)
```

## Out of scope (explicit)

- **The other four `docs/skills/README.md` candidate kinds** (`prompt`,
  `workflow`, `tool_chain`, `command`). Only `agent_plan` (this MVP's
  actual shape, even though the stub file used that name generically) is
  built. `tool_chain` in particular would require a genuinely new
  execution semantic — more than one tool call resolved within a single
  `AgentStep` — which contradicts the audited "exactly one tool per step"
  invariant MVP 7's execution engine currently guarantees; reopening that
  without a proven need is exactly the kind of speculative generality
  CLAUDE.md warns against.
- **A third, fourth, or Nth example skill.** Two is enough to prove the
  registry, boot validation, and selection mechanism all work end to end.
  More skills are a natural, low-risk follow-up once this pattern ships.
- **Self-reported or numeric LLM confidence scoring.** §3 explains why a
  binary null/non-null signal, gated by prompt instruction plus
  independent registry validation, was chosen over a graded confidence
  value.
- **Skill versioning, hot-reload, or any admin/API surface for
  creating/editing skills at runtime.** Skills live in git; changing one
  requires a normal code change + deploy, identical to how `ToolSpec`
  registration already works. No CRUD endpoint, no admin UI.
- **Workspace-scoped or per-workspace-customized skill catalogs.** The
  registry is global, exactly like `tools.registry` today. If a real need
  for workspace-specific skills emerges, that's a separately-scoped future
  MVP, not a silent scope-creep here.
- **Skills invoking or selecting other skills (composability).** A skill's
  step list is flat and static; it never itself triggers another
  `agent.skill_match` call or references another skill by name.
- **Retry or fallback logic specific to the `agent.skill_match` call
  beyond existing `GatewayError` handling.** A failed skill-match call
  degrades to `agent.plan` exactly as a `null` result would — no new retry
  policy, no dedicated circuit breaker.
- **Any change to the execution engine, approval gate, or MVP 7's four
  brakes.** This MVP touches only *how the step list is produced*, never
  how it's executed, timed, budgeted, or cancelled.

## Branch plan

| # | Branch | Contents |
|---|---|---|
| 1 | `feat/mvp8-skills-foundation` | **Done (2026-07-13, merged to main `794572f`).** `skills` kernel app scaffold, `SkillSpec`/`SkillStep`/registry (`register_skill`/`unregister_skill`/`get_skill`/`list_skill_names`/`list_skill_prompt_entries`/`skill_steps_to_plan_dicts`), `skills`' own per-kind step-shape validator (duplicated from, not importing, `agents.planner`'s — §1), boot-time tool-reference validation (`SkillsConfig.ready()`, §2), import-linter contracts, the two example skill definitions (§7) + their registration, tests (registry CRUD, content-hash stability, boot-fail-loud on a dangling tool reference, per-step shape validation, and parity tests proving `skills`' validator and `agents.planner`'s validator accept/reject the same shapes). 3 rounds of Codex code review: round 1 found `SkillStep.payload`'s `MappingProxyType` isn't JSON-serializable (a footgun for branch 2's `finalize_plan()` call) and two shared import-linter contracts missed adding `skills` — both fixed (added `skill_steps_to_plan_dicts()`, added `skills` to the missed contracts); round 2 found `SkillStep.__post_init__`'s `dict(self.payload)` silently coerced non-dict iterables before shape validation ran, breaking parity with `agents.planner._parse_step` — fixed (type-checked before coercion); round 3: 0 findings. 1041 backend tests passing. |
| 2 | `feat/mvp8-skill-selection` | **Done (2026-07-13, merged to main `a3bae79`).** `purpose="agent.skill_match"` gateway call + parser + `seed_ai_defaults` prompt entry, wiring into `create_agent_from_goal()` (§3's five-step flow), `Agent.skill_name`/`skill_content_hash` fields + migration (§6), self-mod guarantee extension (§5), approval-gate invariant test re-run against a skill-originated plan (§4), tests for every degrade path (null / hallucinated name / parse failure / gateway error, each proving `agent.plan` still runs normally afterward) and the match-and-skip path (proving `agent.plan`'s own `ModelUsage` row is *not* written when a skill is used). Codex code review: round 1, 0 findings, direct APPROVE. 1061 backend tests passing. |
| 3 | `feat/mvp8-frontend-e2e` | **Done (2026-07-13, merged to main `80b41fe`).** `AgentDetail` UI addition surfacing `Agent.skill_name` when set ("Ran via skill: weekly_export"); Playwright e2e extending the existing agents section with a skill-matched goal (asserting the resulting steps match the skill's fixed template, not a freeform LLM plan); `MASTER_PLAN.md` §F/§L update, new §X section. Also root-caused and fixed a pre-existing e2e flake in the click-into-agent-detail interaction (confirmed reproducible on unmodified main via a scratch worktree check — not a regression from this branch) with a retry helper (`openAgentDetail()`) applied at all 6 call sites in the smoke test. Codex code review: round 1, 0 findings, direct APPROVE. 1063 backend tests + 287 frontend tests passing, Playwright e2e passing 3/3 consecutive runs against the real Docker stack after the flake fix. |

**MVP 8 COMPLETE — all 3 branches merged to main, all Codex review loops closed.**

## Test strategy

- **Registry** (branch 1): register/get/list round-trip; `unregister_skill`
  test-only isolation; `content_hash` is stable across repeated
  registration of the same `SkillSpec` and changes when any field changes;
  a skill step with an invalid per-kind payload shape fails at
  registration, not silently; **parity tests** feeding the same set of
  valid/invalid per-kind payload shapes to both `skills.catalog`'s
  validator and `agents.planner`'s `_parse_step` assert identical
  accept/reject outcomes, so the two independently-maintained
  implementations (§1) can't silently drift apart.
- **Boot-time validation** (branch 1): a skill referencing a `tool_name`
  not in `tools.registry.list_tool_names()` raises `ImproperlyConfigured`
  at app startup — mirrors however `ApprovalsConfig.ready()`'s
  `DEV_ALLOW_SELF_APPROVAL` boot-fail test is currently structured (reuse
  that mechanism, don't invent a new one).
- **Skill-match gateway call** (branch 2): `FakeAdapter` content-triggered
  fixture for `purpose="agent.skill_match"` (mirrors `router.intent`'s
  established convention) — a specific goal phrase returns a matching
  `skill_name`, an unrelated goal returns `null`, a goal engineered to
  return a **hallucinated/unregistered** skill name still degrades
  cleanly to `agent.plan`. A monkeypatched `GatewayError` on this call
  alone (with `agent.plan` left working normally) proves the degrade path
  doesn't touch the fallback's own behavior — asserts a `ModelUsage` row
  for `purpose="agent.skill_match"` exists and is `success=False`
  (the adapter call itself failed). A separate case monkeypatches the
  skill-match **parser** (not the gateway call) to raise a parse error —
  proves a malformed-but-successful gateway response also degrades to
  `agent.plan`, but asserts the resulting `ModelUsage` row is
  `success=True` (the gateway call itself succeeded; only this code's own
  schema check rejected the response) — mirroring exactly how
  `agent.plan`'s own existing parse-failure test already asserts success
  on its `ModelUsage` row despite the plan being rejected
  (`test_malformed_planner_output_fails_agent_with_planner_error`). A
  third case covers a missing/inactive `agent.skill_match` prompt config
  entirely (no `PromptTemplate` row, or no `active_version`) and asserts
  **no** `ModelUsage` row for `purpose="agent.skill_match"` is written at
  all (resolution failed before any gateway call was attempted) while
  agent creation still succeeds via `agent.plan` — unlike a missing
  `agent.plan` config, which must still fail today exactly as it did
  before this MVP.
- **End-to-end skill path** (branch 2): a skill-matched agent's resulting
  `AgentStep` rows exactly equal the skill's static template
  (kind/description/payload, in order); `Agent.skill_name`/
  `skill_content_hash` are set correctly; **no `ModelUsage` row for
  `purpose="agent.plan"` exists for that agent** (proving the call was
  actually skipped, not just its result ignored); a `ModelUsage` row for
  `purpose="agent.skill_match"` *does* exist (ADR-003, no gateway call
  skips accounting).
- **Non-match regression** (branch 2): a goal that doesn't match any
  skill still produces an ordinary freeform plan identical to MVP 7's
  existing behavior — proves zero regression for every agent created
  before this MVP shipped.
- **Approval-gate invariant** (branch 2): a skill-originated `tool_request`
  step reaches `blocked_on_approval` and the existing `requester != decider`
  permission test passes against it unchanged (§4).
- **Self-modification guarantee** (branch 2): engine/`planner_service`
  never call `skills.catalog.register_skill`/`unregister_skill` (§5),
  monkeypatched to raise if ever called, mirroring the existing tools-
  registry variant of this test exactly.
- **Plan-size bound still applies** (branch 2): a deliberately
  over-`AGENT_MAX_PLAN_STEPS` test `SkillSpec` (registered only within the
  test, via `unregister_skill` cleanup) still fails with
  `plan_too_large` when selected — proves §3's "no changes needed to
  `finalize_plan()`" claim rather than just asserting it.
- **E2E** (branch 3): a goal containing the `weekly_export` trigger phrase
  produces exactly the skill's two-step template, executed through the
  existing UI (task confirm / approval flows unchanged from MVP 7's own
  e2e), against the real Docker stack.

## Risks / notes

- **This MVP's core safety property is that it changes *nothing* about
  execution, only about how the step list is produced.** If plan-review
  proposes any change that lets a skill template influence step status,
  bypass `create_request`, or skip a brake, that is a scope violation
  against ADR-004, not a simplification — flag it back to the user rather
  than resolving it unilaterally, per the same standing rule MVP 7's own
  four brakes were held to.
- **The skill catalog's code-only (no DB table) design is a deliberate
  strengthening over `ai_core.PromptTemplate`/`PromptVersion`'s existing
  "immutable-by-discipline, not code-enforced" pattern** — this was the
  user's own correction during scoping and should not be revisited toward
  a DB-backed model without an explicit, separately-argued reason (e.g. a
  future need for admin-authored skills, which is out of scope here, §
  above).
- **Binary null/non-null skill selection, not graded confidence** (§3) is
  a considered simplification, not an oversight — if plan-review wants to
  push back on this, the burden is on showing why a self-reported
  confidence value would be *more* reliable than the model's own
  null-or-not choice, not just that a graded signal is possible in
  principle.
- **Open parameter/naming values**: the exact `purpose` string
  (`agent.skill_match`), the two example skills' exact names/trigger
  phrases, and the new `Agent` field names (`skill_name`,
  `skill_content_hash`) are proposals, not final — plan-review may rename
  them; the *existence* of each mechanism is the non-negotiable part.
