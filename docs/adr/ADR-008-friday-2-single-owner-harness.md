# ADR-008: Friday 2.0 — Single-Owner System, Agent Harness, Phase Roadmap

## Status
Accepted (2026-07-17)

## Context
The personal kernel (MVP 1–9) is done, but the vision outgrew the original
roadmap: the owner serves multiple firms and wants Friday to be the daily
control plane over them — long-term memory that knows the owner *and* the
firms, an agent harness that drives external CLI coding agents (Claude
Code, Codex CLI, Gemini CLI, Ollama) as workers under the approval gate,
a renewed UI, MCP integrations, and (later) an always-listening local
voice layer. A full architecture analysis (`docs/VISION_FRIDAY_2.md`)
compared three options and the owner approved **Option C**: Friday stays
the control plane, the harness becomes a kernel app, the UI is renewed
in-house. Two latent conflicts surfaced during analysis and are resolved
here: (a) Friday carries a multi-user SaaS surface (signup, invites,
memberships) that will never have a second user; (b) ADR-004's
requester != decider rule assumes two humans — in production today the
owner cannot approve a request created on their own behalf, including
agent-created tool requests (`agents/execution.py` passes
`actor=agent.created_by`), which makes gated agent steps undecidable in a
single-human deployment.

## Decision
1. **Option C is binding.** Friday's kernel (identity, workspace axis,
   gateway, approvals, memory, audit) is the control plane. A new
   `harness` kernel app (ADR-002 rules apply) runs external CLI agents
   headless as workers; their usage is reported through the
   InferenceGateway as ModelUsage (ADR-003 already names "external CLI
   backends' usage reports"). The internal `agents` runtime (MVP 7) stays
   for chat-born light plans; merging it with the harness is re-evaluated
   after Phase 3.
2. **Friday is a single-owner system.** Exactly one human — the owner.
   The multi-user SaaS surface is removed: signup disabled and slated for
   removal, no invite/membership flows, MVP 10 (multi-user tenancy) is
   **cancelled** (the fleet phase may still add a lightweight external-
   installation grouping concept). Identity becomes device trust: on the
   local network a device token/PIN, from outside a single strong key —
   implemented in Phase 4 (UI renewal) replacing the login/signup flow.
   The workspace *axis* (ADR-001) stays untouched: workspaces are firm/
   context profiles and scoping remains the isolation mechanism.
3. **ADR-004 is reinterpreted for one human, not relaxed.** The
   requester != decider (two-person) rule is impossible by definition with
   one human; it is replaced by: every gated action requires an
   **explicit, separate, audited owner decision** — never auto-approved,
   never implied by the action that created the request; machine actors
   (agents, harness, any channel) can never decide; low-trust channels
   still cannot decide (MVP 9); coder != reviewer is preserved by
   **cross-vendor review** in the harness (the implementing CLI agent
   never reviews its own diff). All ADR-004 hard rules stand, and deploy
   automation gets **two** gates: plan approval and deploy approval.
   This decision also formalizes an amendment ADR-004 has carried only in
   prose since MVP 6 (§O's narrowed task-extraction entry; flagged by
   cross-review as a documentation gap): the binding **write-class
   taxonomy** is (a) **gate-class** — anything irreversible or
   outward-facing (delete, deploy, external send, git push/merge,
   filesystem export, and *every* harness write) always requires an
   `ApprovalRequest`; (b) **confirm-class** — reversible, self-scoped,
   in-app rows (e.g. `Task` creation, MVP 6 §3) require an explicit human
   confirm but no `ApprovalRequest`. Moving an action between classes, or
   adding a confirm-class exception, requires a new ADR — this list does
   not grow by precedent.
4. **`work_log` memory class — a deliberately narrow ADR-005 exception.**
   **System-generated activity summaries** — harness runs, gated-tool
   executions, task and deploy outcomes — are recorded as episodic
   `work_log` memories that are **auto-approved but audited and
   deletable**. Auto-approval covers only machine-generated summaries of
   system activity; conversation-derived `work_log` candidates stay
   `pending_approval` until the private-conversation-flag decision
   (MASTER_PLAN §O) is resolved during Phase 1 design. `work_log` rows
   carry the same sensitivity field as every other memory and ADR-005's
   negative contract is unchanged — `private` content never reaches a
   gateway payload. No other memory type may be auto-approved, and no new
   auto-approved classes may be derived from this one without a new ADR.
5. **Voice is re-sequenced, its foundation kept.** ADR-007's shipped
   groundwork (Capability.STT/TTS, transcribe/synthesize, the orb) stands;
   the remaining voice work moves to Phase 6. Phase 6 design constraints
   fixed now: wake-word detection is **local** (openWakeWord/Porcupine
   class — audio never leaves the device; processing starts only after
   the wake word), STT/TTS stay local (faster-whisper, Piper). The
   animated "Friday" visual identity layer comes **after** voice works.
6. **Integrations are bridges, never drivers.** Home/device control goes
   through a Home Assistant API/MCP bridge (Phase 5) — Friday never
   implements device drivers. An OpenAI-compatible facade endpoint may
   expose chat (only chat — no tool calls) as a low-trust secondary
   channel for mature clients like Open WebUI.
7. **Runner abstraction.** Harness workers run wherever a runner is
   registered; the first runner is the owner's Mac. CLI-agent headless
   auth terms (subscription vs API key) are settled in the Phase 2 brief,
   with an API-key fallback designed if vendor terms block automation.

## Consequences
The roadmap after MVP 9 is reorganized into Phases 0–6 (MASTER_PLAN §AA;
detail in `docs/VISION_FRIDAY_2.md`, now binding as approved vision).
Phase 0 lands the decisions in code: firm profiles on Workspace, an
Ollama embedding provider, the model-tier routing skeleton (§J), and the
approvals machine-requester fix so agent-created requests become decidable
by the owner in production. `DEV_ALLOW_SELF_APPROVAL` becomes obsolete
once decision 3 is implemented and is removed with it. MASTER_PLAN §O
gains/resolves the matching open-decision entries; §Z gains the local
wake-word and visual-identity notes. ADR-004/005/007 are amended by this
ADR where stated, not replaced; every other rule in them stays in force.

## Implementation notes (2026-09-27)
Dated status notes on how the decisions above landed; they record
implementation facts and open items, not new decisions.

- **Decision 7 (CLI auth) — finalized in Phase 3 branch 3.** Headless CLI
  agents authenticate with API keys only: `ANTHROPIC_API_KEY` for Claude
  Code, `CODEX_API_KEY` for Codex CLI (both in `.env.example`, injected per
  agent by `backend/harness/execution.py`). The ChatGPT-subscription OAuth
  ("Sign in with ChatGPT") path is never used. See the Phase 3 brief,
  "Open question resolved: Codex CLI vendor terms".
- **Decision 3 (two deploy-automation gates) — made concrete.** Phase 3's
  implementation pipeline has **Gate A** (plan approval, requested at job
  creation) and **Gate B** (push approval, after cross-vendor review);
  the deploy gate proper lands with Phase 5.
- **Decision 4 (`work_log` producers) — partially implemented.** Today the
  only producer is harness discovery (`record_work_log()` called from
  `backend/harness/execution.py` on job success). The remaining producers
  are scheduled: implementation/push outcomes in the Phase 3 closeout
  (`feat/p3-implementation-api`); gated-tool executions, task outcomes and
  deploy outcomes in Phase 5 (MASTER_PLAN §AA outline).
- **Decision 2 (single owner, device-trust identity) — landed in Phase 4
  branch 2.** `User.is_owner`, trusted devices with pairing codes (LAN PIN,
  strong code, remote key), device-bound JWTs, default-deny
  `request_channel()` with `IsWebChannel`/`IsOwner`; signup and password
  login are gone (`/enroll`); the owner password survives only as the
  Django-admin break-glass, routed when `DJANGO_ADMIN_ENABLED`.
- **Decision 6 (facade: chat only, low trust) — landed in Phase 4 branch
  6.** `/v1/chat/completions` + `/v1/models` behind `openai`-kind PATs
  (`CHANNEL_OPENAI`), router restricted to chat/memory/knowledge intents,
  never deciding or reaching tools.
- **Resolved — decision 3 "delete" vs owner deletes (flagged here for
  the Phase 4 brief).** At the time, the owner hard-deleted memory,
  knowledge documents and tasks directly over the web channel without an
  `ApprovalRequest`. ADR-009 (Phase 4) settles it: owner deletes of
  self-scoped rows are confirm-class soft deletes with a 30-day trash
  (`deleted_at`), and permanent removal is the gate-class `purge_trash`.

## Implementation notes (Phase 5 closeout, 2026-11-20)

- **Decision 3 (deploy-automation gates) — complete.** The third owner
  decision landed in Phase 5 branch 6: the `deploys` app, a per-workspace
  `DeployTarget` allowlist, the system-created `deploy` gate showing the
  full plan (full SHAs, SSH destination, CI conclusion, health plan,
  rollback SHA), a forced-command SSH runner that sends verbs only, and a
  health window. Automatic rollback is part of the same decision only by
  ADR-010's explicit extension (previous recorded SHA, inside the stated
  window, after three consecutive failures, `auto_rollback` opt-out); a
  later manual rollback is a new request and a new decision. Opening a PR
  for a pushed harness branch is its own gate (`github_open_pr`, branch
  5); merging stays a human action outside Friday.
- **Decision 4 (`work_log` producers) — complete.** Every producer now
  writes through `memory.services.record_work_log`: harness discovery
  (`harness/execution.py`), implementation/push outcomes
  (`harness/implementation_execution.py`), gated-tool executions
  (`tools/work_log.record_tool_outcome`: vault export, connector writes,
  deploy runs via `deploys/outcomes.py`) and task outcomes
  (`tasks/services.py`). Content is Friday-authored bookkeeping only, never
  connector or remote output. The closeout e2e
  (`backend/ops/tests/test_closeout_e2e.py`) drives harness job → PR →
  deploy and asserts the push and deploy rows are visible to the owner.
- **Preflight.** `manage.py preflight` (`backend/ops/`) is the live
  "done" check named in MASTER_PLAN's "Roadmap update 2026-11-20"; later
  phases add their own checks to it.

## Implementation note — decision 5 landed (2026-11-20, Phase 6 closeout)

Decision 5 (voice: local wake word, local STT/TTS, visual identity after
voice works) is implemented by Phase 6
(`docs/prompts/phase-6-voice-identity/brief.md`, ADR-011, ADR-015):
the "Hey Friday" detector (openWakeWord + Silero VAD on `onnxruntime-web`)
runs in the browser tab and no audio leaves the device before it fires
(push-to-talk stays the default until Phase 11's false-activation budget,
d3); STT/TTS run on the owner's Mac behind the `openai_compatible` adapter
(faster-whisper / Piper via speaches or the `deploy/voice/` sidecar); the
visual identity layer came last as HUD v1 (`/hud`), after the push-to-talk
loop and the wake word worked. Decision 3 is unchanged by voice: a spoken
utterance is never the explicit decision and never a confirm-class
confirmation (ADR-011 decision 5, ADR-015); the closeout e2e proves a voice
task draft becomes a task only through the card's click. Pending owner
steps (the custom `hey_friday` model, the Mac measurements) are listed in
the Phase 6 brief.
