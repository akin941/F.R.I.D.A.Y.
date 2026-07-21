# ADR-008: Jarvis 2.0 — Single-Owner System, Agent Harness, Phase Roadmap

## Status
Accepted (2026-07-17)

## Context
The personal kernel (MVP 1–9) is done, but the vision outgrew the original
roadmap: the owner serves multiple firms and wants Jarvis to be the daily
control plane over them — long-term memory that knows the owner *and* the
firms, an agent harness that drives external CLI coding agents (Claude
Code, Codex CLI, Gemini CLI, Ollama) as workers under the approval gate,
a renewed UI, MCP integrations, and (later) an always-listening local
voice layer. A full architecture analysis (`docs/VISION_JARVIS_2.md`)
compared three options and the owner approved **Option C**: Jarvis stays
the control plane, the harness becomes a kernel app, the UI is renewed
in-house. Two latent conflicts surfaced during analysis and are resolved
here: (a) Jarvis carries a multi-user SaaS surface (signup, invites,
memberships) that will never have a second user; (b) ADR-004's
requester != decider rule assumes two humans — in production today the
owner cannot approve a request created on their own behalf, including
agent-created tool requests (`agents/execution.py` passes
`actor=agent.created_by`), which makes gated agent steps undecidable in a
single-human deployment.

## Decision
1. **Option C is binding.** Jarvis's kernel (identity, workspace axis,
   gateway, approvals, memory, audit) is the control plane. A new
   `harness` kernel app (ADR-002 rules apply) runs external CLI agents
   headless as workers; their usage is reported through the
   InferenceGateway as ModelUsage (ADR-003 already names "external CLI
   backends' usage reports"). The internal `agents` runtime (MVP 7) stays
   for chat-born light plans; merging it with the harness is re-evaluated
   after Phase 3.
2. **Jarvis is a single-owner system.** Exactly one human — the owner.
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
   animated "Jarvis" visual identity layer comes **after** voice works.
6. **Integrations are bridges, never drivers.** Home/device control goes
   through a Home Assistant API/MCP bridge (Phase 5) — Jarvis never
   implements device drivers. An OpenAI-compatible facade endpoint may
   expose chat (only chat — no tool calls) as a low-trust secondary
   channel for mature clients like Open WebUI.
7. **Runner abstraction.** Harness workers run wherever a runner is
   registered; the first runner is the owner's Mac. CLI-agent headless
   auth terms (subscription vs API key) are settled in the Phase 2 brief,
   with an API-key fallback designed if vendor terms block automation.

## Consequences
The roadmap after MVP 9 is reorganized into Phases 0–6 (MASTER_PLAN §AA;
detail in `docs/VISION_JARVIS_2.md`, now binding as approved vision).
Phase 0 lands the decisions in code: firm profiles on Workspace, an
Ollama embedding provider, the model-tier routing skeleton (§J), and the
approvals machine-requester fix so agent-created requests become decidable
by the owner in production. `DEV_ALLOW_SELF_APPROVAL` becomes obsolete
once decision 3 is implemented and is removed with it. MASTER_PLAN §O
gains/resolves the matching open-decision entries; §Z gains the local
wake-word and visual-identity notes. ADR-004/005/007 are amended by this
ADR where stated, not replaced; every other rule in them stays in force.
