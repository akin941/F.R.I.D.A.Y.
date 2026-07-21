# ADR-004: Approval Gate Rules

## Status
Accepted

## Context
Jarvis runs agents that can eventually write files, commit code, send messages and
trigger workflows. Trust must be earned per tool, never assumed.

## Decision
Write/delete/deploy/external-send actions require an ApprovalRequest approved by a
human. Hard rules that no configuration may relax: no unapproved file deletion,
production deploy, external data send, or git push/merge; agents can never modify
their own definition or permissions; the coder agent never reviews its own code;
every tool call and cost is logged. Auto-approval may later be enabled per
low-risk tool per user, based on observed error rates — never for the hard rules.

## Consequences
Some friction on every write action, traded for the ability to grant agents real
capabilities safely. The approval gate ships in MVP 5, before any agent runtime.
