# ADR-004: Approval Gate Rules

## Status
Accepted

## Context
Friday runs agents that can eventually write files, commit code, send messages and
trigger workflows. Trust must be earned per tool, never assumed.

## Decision
Write/delete/deploy/external-send actions require an ApprovalRequest approved by a
human. Hard rules that no configuration may relax: no unapproved file deletion,
production deploy, external data send, or git push/merge; agents can never modify
their own definition or permissions; the coder agent never reviews its own code;
every tool call and cost is logged. Auto-approval may later be enabled per
low-risk tool per user, based on observed error rates — never for the hard rules.

> Note (2026-07-17): amended by ADR-008 decision 3 — for a single-owner
> system the requester != decider rule is replaced by an explicit, separate,
> audited owner decision (machine actors and low-trust channels can never
> decide; coder != reviewer is kept via cross-vendor review), and the binding
> write-class taxonomy (gate-class vs confirm-class) is formalized. All hard
> rules above stay in force.

## Consequences
Some friction on every write action, traded for the ability to grant agents real
capabilities safely. The approval gate ships in MVP 5, before any agent runtime.
