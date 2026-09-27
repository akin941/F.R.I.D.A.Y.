# Phase 3 — Harness v2 (implementation)

> Status (2026-09-27): **in progress — branches 1–4 shipped
> (2026-07-20 → 07-22); closeout re-planned after the 2026-09-27 plan
> audit into branches 5–8 (see branch table and "Closeout re-plan
> 2026-09-27").** Original plan-review status follows.
>
> Plan-review status: **approved for implementation (plan-review round 8: 0
> CRITICAL/0 HIGH/0 MEDIUM, explicit "move to implementation"
> recommendation).** Full 8-round arc: round 1: 4H/2M/2L; round 2:
> 1 CRITICAL/2H/2M; round 3: 1 CRITICAL/1H/2M/1L; round 4: 0
> CRITICAL/2H/1L; round 5: 0 CRITICAL/2H/1L; round 6: 0 CRITICAL/2H/1L;
> round 7: 0 CRITICAL/0H/1M/1L; round 8: clean. No CRITICAL after round
> 3; no HIGH after round 6. The push/result-file design specifically
> (branch 1's scope) needed 6 consecutive tightening passes (rounds
> 2–7) as each fix revealed a narrower residual gap — explicit-URL
> hardening → clean-clone push → bundle-only extraction → named-ref
> bundling + process quiescence → create-only compare-and-swap push +
> confirmed quiescence loop — the longest, most heavily scrutinized
> single design decision in this repo's plan-review history so far, for
> the first phase that introduces real repo-write authority. Four user
> checkpoints along the way: self-modification guard (round 1, chose
> hard-block); push security round 1 (round 2's CRITICAL, initially
> chose harden-the-push-command); push security round 2 (round 3 found
> that fix incomplete, switched to push-from-a-separate-clean-clone);
> round-5 continuation (chose to keep fixing directly and re-review
> rather than move to implementation with open items — vindicated by
> round 8 eventually landing clean).**
> Parent decisions: ADR-004 (approval gate rules, write-class taxonomy),
> ADR-008 decision 3 (gate-class vs confirm-class; cross-vendor review),
> `docs/VISION_JARVIS_2.md` §4/§6/§8, MASTER_PLAN §AA row 3.
> Builds directly on Phase 2 (`docs/prompts/phase-2-harness-v1/brief.md`,
> shipped 2026-07-20): `harness` kernel app, `HarnessRepo` allowlist,
> `HarnessJob` (read-only discovery), the Docker-container brake system,
> `ai_core.gateway.record_external_usage()`.

## Goal / value

Extend the harness from "read-only discovery, briefing only" to real,
approved, tested, cross-reviewed feature development: a curator requests
an implementation from a goal (optionally following a Phase 2 discovery
run's briefing), a CLI agent implements it in an isolated git worktree,
runs the repo's own tests, a *different-vendor* CLI agent reviews the
diff, and — only after an explicit human approval — the branch is pushed
to the remote. No deploy, no merge to `main`, no direct write to any
branch a human is working on. The usable outcome (MASTER_PLAN §AA):
"Add this feature to firm X's repo" → an approved, tested, reviewed
branch, ready for the owner to open a PR from by hand (Phase 5 adds
deploy automation on top of a merged branch — out of scope here).

## Boundary vs existing systems

- **Two ApprovalRequest gates, both gate-class (ADR-008 decision 3: "every
  harness write" + "git push/merge" are both named explicitly in the
  binding write-class taxonomy)** — not one. **Gate A (plan approval)**
  gates the *decision to let a CLI agent write inside an isolated
  worktree at all* — nothing has been written yet when this is requested;
  the payload is a goal + optional repo/discovery-job reference, not a
  diff. **Gate B (push approval)** gates the *decision to let a
  passing-tests, cross-reviewed branch leave the ephemeral worktree and
  reach the remote* — requested only after implementation + tests +
  review have all completed, with the actual diff summary and review
  verdict in the payload. These are Jarvis's own two gates for *this
  phase*, distinct from VISION §4's "gate #2 (deploy approval)", which is
  Phase 5's separate, later gate over the deploy action once a branch is
  already merged — not to be conflated. Neither gate is a rubber stamp of
  the other: an approved Gate A does not imply Gate B, since Gate A
  approves *intent to attempt*, Gate B approves the *actual resulting
  diff*. Plan-review round 1 (Codex) confirmed this two-gate split
  actually satisfies ADR-004/ADR-008 decision 3's taxonomy as designed.
- **Both gates are real, registered `ToolSpec`s (`tools.registry`) —
  round-1 correction, O-1 resolved.** An earlier draft of this brief
  assumed `agents`' own `tool_request` step bypasses `tools.registry`
  and called `approvals.services.create_request()` directly with no
  registered tool; that was wrong. `create_request()` rejects any
  `tool_name` that doesn't resolve to a registered, `gated=True`
  `ToolSpec` (`approvals/services.py:190-192`) — `_dispatch_tool_request_step`
  only works *because* its `tool_name` already resolves to something like
  `vault_export`. Both `harness_implement` (Gate A) and `harness_push`
  (Gate B) are registered `ToolSpec`s mirroring `tools/export.py`'s exact
  shape: `create_execution(approval_request)` creates the durable row
  (for Gate A: transitions `HarnessImplementationJob` into the running
  worktree phase; for Gate B: records the approval and marks the job
  ready to push) *inside* the approval's own transaction, `dispatch(id)`
  schedules the actual Celery task via `transaction.on_commit`, mirroring
  `create_vault_export_run`/`dispatch_vault_export_run` line for line.
  A **rejected or expired** decision has no `dispatch` to run — that path
  stays signal-driven: `harness/signals.py`'s `post_save` receiver on
  `ApprovalRequest` (mirrors `agents/signals.py`'s receiver exactly) fires
  on any non-pending status, and only the `approved` branch matters for
  `ToolSpec.dispatch` — `rejected`/`expired` instead flip
  `HarnessImplementationJob.status` straight to a terminal failure state
  from the signal receiver itself, no Celery task involved. **The
  receiver must actually be connected (round-3 MEDIUM finding):**
  `harness/apps.py` currently has no `ready()` hook at all — unlike
  `agents/apps.py:8`, which is exactly where `agents/signals.py`'s
  receiver gets connected once at process startup. Branch 2 adds
  `HarnessConfig.ready()` importing and connecting `harness/signals.py`'s
  receiver the same way; without it, a Gate A/B rejection or expiry
  would leave the implementation job silently stuck forever; a direct
  test asserts the receiver actually fires on a real `ApprovalRequest`
  save, not just that the receiver function exists in isolation.
- **Self-modification guard (ADR-004: "agents can never modify their own
  definitions or permissions"), hard-block, not a review-time check
  (round-1 HIGH finding, user decision 2026-07-20).** A `HarnessRepo`
  whose resolved git remote matches this codebase's own — a new
  `HARNESS_SELF_REPO_IDENTIFIERS` setting (list of remote URLs recognized
  as "this Jarvis installation itself", compared via the dedicated
  canonical normalizer below — **not** `harness/serializers.py`'s
  existing credential-rejection parsing, which only rejects embedded
  credentials and isn't a canonical-form normalizer) — can still be
  registered and used for Phase 2 read-only discovery (unchanged, no
  risk there), but
  `create_harness_implementation_job()` (branch 2) refuses to create the
  row at all — `HarnessInvalidTransition`, before Gate A is ever
  requested, not a check the review pass or Gate B could be talked out
  of. Applies to both `HarnessImplementationJob` and `HarnessReview`
  (a review pass reading this codebase's own approval/gate code is lower
  risk than writing to it, but still refused outright — simpler than
  arguing the line case by case).
- **New `HarnessImplementationJob` model, not an extension of `HarnessJob`.**
  Phase 2's `HarnessJob` docstring is explicit: "a single read-only
  discovery run… no write, no branch push, no deploy." Its state machine,
  fields, and brake system were designed for exactly that shape.
  Implementation has a materially different lifecycle (two approval
  gates, a worktree instead of a throwaway read-only clone, a linked
  review, and — round-1 HIGH finding, "missing entirely" — real fields
  for the artifacts a push actually needs). `HarnessImplementationJob`
  FKs to `HarnessRepo`, optionally FKs to the `HarnessJob` (discovery
  run) it followed from (**O-2, resolved by plan-review: optional** —
  Gate A's own approval payload always carries the actual goal/plan
  regardless of whether discovery ran first, so requiring the link is
  policy friction, not a security boundary), and to both `ApprovalRequest`s
  (Gate A, Gate B — nullable until each is actually requested). New
  fields beyond Phase 2's `HarnessJob` shape (round-1 "missing entirely"):
  `branch_name` (`harness/p3-<job-id>` — **O-3, resolved by plan-review**:
  job-id-keyed uniqueness is sufficient for this phase, a configurable
  per-repo prefix is a later refinement only if a real repo's own
  conventions need it; if the branch already exists on the remote, the
  push fails closed — never force-pushed, never silently renamed),
  `base_commit_sha`, `produced_commit_sha`, `diff_summary` (gateway-
  produced, same fencing discipline as the review's own summary — see
  below), `changed_files` (JSONField list), `test_result` (JSONField —
  schema below), `worktree_path` + `worktree_expires_at` (round-1 HIGH
  finding: the worktree's lifetime, below), `push_result`.
- **New `HarnessReview` model** (not a repurposed `HarnessJob`): FK to the
  `HarnessImplementationJob` it reviews, `agent` (must differ from the
  implementation job's `agent` — the mechanical cross-vendor-review
  enforcement ADR-008 decision 3 requires, enforced at the service layer,
  not left as convention; there is no existing review-verdict model
  anywhere in the codebase to reuse — confirmed during this brief's
  research pass), `verdict` (`approved`/`changes_requested`),
  `summary`/`findings`/`raw_transcript` as this model's own fields (round-1
  LOW correction: `HarnessJob.briefing` is a single `JSONField`, not
  separate columns — `HarnessReview` is free to choose either shape;
  separate typed fields read more naturally for a model whose whole
  purpose is those three pieces of content, but the *content/fencing
  discipline* — gateway-produced, low-trust-input-fenced, R2 mitigation —
  is what's actually reused from branch 3 Phase 2's
  `_produce_briefing_summary`/`neutralize_fence` functions, not the DB
  schema). The implementation job's own `diff_summary` field (above) uses
  the identical gateway-summarization call, since "the CLI's own account
  of what it changed" is exactly as much low-trust content as "the CLI's
  own account of what it found."
- **The shared "launch container → poll → brake → teardown → record
  usage" engine is NOT extracted in this phase (round-1 MEDIUM finding,
  accepted — reverses this brief's own earlier draft).** `HarnessJob`'s
  execution engine (`harness/execution.py`, 7 rounds of hard-won Codex
  findings baked into its comments) stays as-is; `HarnessImplementationJob`
  and `HarnessReview` get their own narrowly-scoped execution functions
  (branch 2 and branch 3 respectively, each built on branch 1's runner
  primitives but not sharing a common engine with `HarnessJob`'s),
  accepting some duplication of the brake-system *shape* (timeout,
  cancel-poll, container teardown) rather than
  risking a premature abstraction over a system that has only ever had
  one real call site until now. Extraction is explicitly deferred to
  whichever later phase has three real call sites to generalize from
  correctly, once their actual differences (mount mode, artifact
  retention, what "success" means per job type) are fully known rather
  than guessed at design time.
- **Writable worktree + push credentials — new ground, no existing
  primitive to build on** (confirmed during this brief's research pass):
  `harness/runner.clone_repo` today does a read-only, shallow,
  single-branch clone into a throwaway temp dir, and the container
  mounts it **read-only**. Phase 3 adds, for `HarnessImplementationJob`
  only (never for `HarnessJob`/`HarnessReview`, which both stay read-only
  exactly as today — a source-level test asserts this split directly,
  test strategy below): a full (non-shallow) clone, a new branch checked
  out inside it, a **read-write** container mount for this job type only,
  and — after the container exits with a commit made — a **host-side**
  `git push` using credentials resolved from env (the `clone_env`-from-env
  convention `runner.py`'s docstring already describes but no caller
  populates yet — this phase finally has a real credential to resolve:
  an SSH deploy key or PAT scoped to the registered repo). The push runs
  on the host, after the container has already exited and been torn
  down, over the finished worktree directory — never inside the
  container itself, and never with the CLI agent's own process able to
  touch remote credentials (mirrors the existing "clone credential stays
  host-side" rule, extended to push).
- **Push happens from a separate, host-only clean clone, using
  bundle-only extraction and a direct SHA push — never `git am`/
  `format-patch`, never a checkout in the agent's own worktree (round-3
  CRITICAL finding fixed by switching to a clean clone; round-4 HIGH
  finding tightens the extraction mechanism itself, user decision
  2026-07-20 covers the overall direction, the round-4 tightening is a
  refinement of it, not a new fork).** Round 2's fix (explicit remote
  URL, `--no-verify`, sanitized env) closed the specific `pre-push`-hook
  and rewritten-remote-URL cases but left `.git/config` settings like
  `credential.helper`/`core.sshCommand`/`url.*.insteadOf` still reachable
  from a push run in the agent's own worktree (round 3). Round 3's own
  first fix (bundle create **or** `git format-patch`, applied via
  `git am`) was itself too broad (round 4): `format-patch` is
  config-sensitive (`format.signatureFile` and other format-affecting
  config), `git am` runs hooks (`post-applypatch`, not skippable), and
  `git am` mints **new** commits with different SHAs — directly
  contradicting this brief's own "pushed SHA must equal
  `produced_commit_sha`" consistency check. The tightened design is
  **bundle-only, no patch/am step at all, bundling a named ref, never a
  bare SHA (round-6 HIGH fix — `git bundle create` rejects a bare
  revision with no accompanying ref: Git's own docs show it refusing to
  create an "empty bundle" for exactly that form)**: the host first
  verifies, via plumbing against the agent's own worktree,
  that `refs/heads/<branch_name>` (the branch the implementation
  container committed to) resolves to exactly `produced_commit_sha`;
  only then `git bundle create <bundle-path> refs/heads/<branch_name>`
  inside the agent's worktree (bundling the named ref the host just
  verified, not a bare commit object — a local, non-networked operation
  regardless of which valid form it's given); `git fetch <bundle-path>
  refs/heads/<branch_name>` inside the freshly host-only-cloned copy (a
  bundle is just a packaged set of objects plus refs — fetching one
  involves no hooks and no working-tree checkout at all); the host then
  verifies, entirely with plumbing commands against the clean clone's own
  object database, that `base_commit_sha` is an ancestor of the fetched
  commit (`git merge-base --is-ancestor`) and that the fetched object's
  own SHA is exactly `produced_commit_sha` (a direct string comparison,
  not inferred); only then `git push <explicit-remote-url>
  produced_commit_sha:refs/heads/<branch_name>
  --force-with-lease=refs/heads/<branch_name>:` (round-7 MEDIUM fix — a
  plain push here is not actually create-only: it would succeed, not
  fail closed, if the branch already exists on the remote as an ancestor
  of `produced_commit_sha`, contradicting this brief's own "existing
  remote branch → fail closed, never force-push" policy stated above;
  `--force-with-lease=<ref>:` with an empty expected value is Git's own
  documented form for "this push must create the ref, it must not
  already exist" — a real compare-and-swap, not a convention) — pushing
  a raw commit SHA straight to a new branch ref, never a checked-out
  local branch tip in the clean clone, so the clean clone's own working
  tree is never populated or checked out at all (no `post-checkout`
  hook, nothing to check out). The clean clone's `.git/config`/`.git/hooks` were written
  entirely by host code and never touched by anything agent-controlled
  at any point in this sequence — a structural property, not an
  enumerated denylist. A direct test plants a malicious `pre-push` hook
  AND a rewritten `.git/config` (`credential.helper`, `core.sshCommand`,
  `url.*.insteadOf`, remote URL) in the agent's own worktree fixture and
  asserts none of it is ever consulted, because the push subprocess never
  runs with that worktree as its working tree or `--git-dir` at all; a
  second test asserts the pushed ref's SHA is exactly
  `produced_commit_sha`, never a newly-minted commit from an apply step.
- **Worktree lifetime across the Gate-B wait (round-1 HIGH finding).**
  Unlike Phase 2's throwaway read-only clone (deleted the moment the
  container exits), the finished worktree must survive from
  implementation completion through however long the owner takes to
  decide Gate B — potentially across a worker restart. `worktree_path`
  is persisted on `HarnessImplementationJob` (not just held in a Celery
  task's local variables), `worktree_expires_at` bounds how long an
  unresolved worktree is kept (a new `HARNESS_WORKTREE_RETENTION_HOURS`
  setting; the recovery scan — branch 4 — sweeps and cleans up any
  worktree past this deadline whose job never reached a terminal state,
  failing the job as `worktree_expired` rather than leaving an orphaned
  directory or attempting a push against a worktree that might have gone
  stale relative to the remote in the meantime).
- **The wrapper runs the test command itself and derives the result from
  its own subprocess exit code — never the CLI agent's self-report
  (round-2 HIGH finding, correcting round 1's own fix).** Round 1's fix
  ("a wrapper-owned JSON result on top of whatever the CLI itself
  prints") still left room to read the "owned" JSON from something the
  CLI agent could itself influence (its own transcript, a file it wrote).
  That's the wrong trust boundary: the wrapper — code Jarvis controls,
  baked into the container image, never the CLI's own output — invokes
  the test command **as its own subprocess**, after the CLI agent's
  invocation has already exited, and derives `passed` from that
  subprocess's real exit code, never from parsing prose or a
  CLI-authored file. `commit_sha`/`changed_files` are likewise computed
  by the wrapper running `git` itself against the worktree state left
  behind, never read from anything the agent wrote about what it did.
  The test *command* itself is a new, explicit, curator-set
  `HarnessRepo.test_command` field (nullable — a repo with none
  configured fails every implementation job closed as
  `test_command_not_configured`, never silently skips verification and
  proceeds anyway). This is deliberately not "a general-purpose
  test-runner abstraction across arbitrary repo tooling" (still out of
  scope) — it's one opaque, curator-authored shell command string per
  repo, run exactly once, with its exit code trusted and nothing else
  about it interpreted. Wrapper-emitted JSON schema: `{"tests": {"ran":
  bool, "exit_code": int, "command": str}, "commit_sha": str,
  "changed_files": [str, ...]}` (`passed` is `exit_code == 0`, derived by
  Jarvis's own parsing code, not a field the wrapper gets to assert
  directly). Missing/malformed JSON, or `tests.ran=false` (e.g. no
  `test_command` configured), fails the job closed as
  `test_result_unavailable`/`tests_failed` — mirrors branch 2 Phase 2's
  "an agent without usable reported cost is not selectable" fail-closed
  precedent exactly. No Gate B request, no review dispatch, for a job
  whose tests didn't verifiably pass by the wrapper's own hand.
- **The result is read from a dedicated file, written only after the
  wrapper has forcibly quiesced every other process in the container —
  strict sequencing alone is not enough (round-3 HIGH finding; round-4's
  uid-separation fix reverted at round 5 for conflicting with Phase 2's
  `--user {host_uid}:{host_gid}` convention; round-5's "blocking
  subprocess returned" reasoning itself found insufficient at round 6:
  the CLI agent or the test command can fork/daemonize a grandchild that
  detaches from the wrapper's own direct-child wait — that grandchild is
  never `waitpid()`-reaped by "the subprocess call returning" and can
  keep running, same uid, free to race the wrapper's own result-file
  write).** The runner's existing log-capture path (`runner.capture_logs`,
  reused from Phase 2) mixes the CLI agent's own output with the test
  command's own output — either could freely print text that *looks
  like* the expected result JSON, so the wrapper writes its result to a
  dedicated path rather than stdout (round 3), sequenced after both the
  CLI agent and the test command have returned (round 5) — necessary but
  not sufficient, since "the process the wrapper directly waited on
  exited" doesn't mean every process *it* may have spawned did too. The
  wrapper's actual last two steps, in order (round-7 LOW fix — spelled
  out precisely, not "send one signal and hope"): **(1) forcibly
  terminate every remaining process in the container and confirm it**:
  `kill(-1, SIGKILL)` from PID 1 (a same-uid signal, permitted under
  ordinary Unix signal rules with no elevated privilege needed, and
  effective against a `setsid`'d/daemonized grandchild exactly because
  the container's PID namespace, unlike a bare host process tree, has
  nowhere else for it to hide or survive — `SIGKILL` cannot be caught,
  blocked, or ignored by anything still alive to receive it), followed
  by a scan-and-repeat loop against `/proc` — not a single one-shot
  snapshot, which could race a process that was still forking at the
  moment of the snapshot — until the wrapper itself observes zero
  processes other than its own PID still present (round-8 polish: PID 1
  also reaps with `waitpid(-1, WNOHANG)` between scans, so a killed
  child that's become a zombie is actually collected and disappears from
  `/proc` rather than lingering and stalling the "zero remaining"
  check); **(2) only then** write the result file. **Superseded by branch
  1's own implementation-time code review (rounds 3, 6, 8, 10) — this
  paragraph originally specified `O_CREAT|O_EXCL|O_NOFOLLOW` (fail closed
  if anything is already at that path); that turned out to be the wrong
  primitive and was reverted during round 3 in favor of what actually
  shipped, documented here for consistency with the code:** an agent can
  plant a believable decoy result file *well before* quiescence ever
  runs, at any point during its own writable run — `O_EXCL` would make
  the wrapper's own legitimate write *fail* against that decoy, leaving
  the decoy as the only content the host ever reads, exactly backwards
  from "fail closed." The actual guarantee comes from ordering, not from
  refusing to overwrite: once step (1) has confirmed zero other processes
  remain, the wrapper is the sole surviving actor and can safely `chmod
  u+rwx` the containing directory (round 8 — needed because the agent
  could also have stripped write/execute permission from it as a
  *filesystem-state*, not process, attack, which `set -e` would otherwise
  let abort the script before ever reaching the write), `rm -f` whatever
  is there (stale decoy, symlink, or nothing), and write the real result.
  Last-writer-wins is safe exactly because quiescence has already
  eliminated every other writer. Round 6 additionally found that the
  wrapper's own post-quiescence `git status`/`diff` reads can themselves
  trigger agent-configured repo hooks (`core.fsmonitor`), spawning a
  process *after* step (1) already ran — closed with `-c core.fsmonitor=`
  on those reads plus a second quiescence pass immediately before the
  write. Round 10 found the dirty-worktree check itself could be defeated
  by `git update-index --skip-worktree` or `status.showUntrackedFiles=no`
  — closed by clearing skip-worktree bits and forcing
  `status.showUntrackedFiles=all` before trusting an empty `git status`.
  Same uid throughout, same container-launch convention Phase 2 already
  established — no privileged/unprivileged split, no Dockerfile user
  changes, no conflict with `--user {host_uid}:{host_gid}`. The shipped
  regression suite (`harness/tests/test_runner.py`) covers: a malicious
  `pre-push` hook and rewritten `.git/config` in the agent's worktree,
  asserting the push subprocess never runs there at all; the pushed ref's
  SHA matching `produced_commit_sha` exactly, never a re-minted commit;
  a missing/malformed/symlinked/FIFO result path failing closed via
  `lstat`; a non-SSH remote and an unconfigured push credential both
  failing closed rather than falling back to ambient host credentials.
- **`HARNESS_SELF_REPO_IDENTIFIERS` matching uses a dedicated canonical
  normalizer, not exact string match (round-2 MEDIUM finding, O-5
  resolved).** `harness/serializers.py`'s existing URL parsing only goes
  as far as rejecting embedded credentials — it is not a canonical
  normalizer and this brief no longer claims otherwise. A new,
  purpose-built normalizer reduces both a `HarnessRepo.git_url` and each
  configured self-repo identifier to a comparable `host/owner/repo` form
  (lowercased, `.git` suffix and trailing slashes stripped, scp-like
  (`git@host:owner/repo`) and `https://host/owner/repo` forms both
  resolved to the same canonical string) before comparing — so
  `git@github.com:akin941/jarvis-os.git` and
  `https://github.com/akin941/jarvis-os` are correctly recognized as the
  same repo. Local/`file://` paths (if ever a valid `git_url` — Phase 2's
  serializer doesn't explicitly forbid them) normalize to their resolved
  absolute path. A direct test table drives this: known-equivalent URL
  pairs must normalize identically; the hard-block fires on any of them.
- **Agent capability matrix, not a single flat selectable set (round-1
  MEDIUM finding).** Phase 2's `_SELECTABLE_AGENTS` controls discovery-job
  selectability only. Phase 3 needs `_SELECTABLE_DISCOVERY_AGENTS`
  (unchanged, `{CLAUDE_CODE}`), `_SELECTABLE_IMPLEMENTATION_AGENTS`, and
  `_SELECTABLE_REVIEW_AGENTS` as separate sets — conflating them would
  let a UI or service-layer bug offer an agent as a reviewer before its
  vendor terms are actually verified for that specific capability, not
  just for discovery. Codex CLI joins `_SELECTABLE_REVIEW_AGENTS` (and,
  separately, `_SELECTABLE_IMPLEMENTATION_AGENTS`) only once branch 3's
  own vendor-terms verification clears it for automated use — the two
  capabilities are verified and granted independently, not as a package,
  since a vendor's terms could plausibly distinguish "read a diff and
  comment" from "write and commit code" even if this brief expects them
  not to in practice.
- **Worktree cleanup on every terminal state, not only the expiry sweep
  (round-2 MEDIUM finding, expanded per round-3 MEDIUM finding).** Every
  path that ends `HarnessImplementationJob` in a terminal state cleans up
  its own worktree immediately (mirrors Phase 2's
  `_safe_cleanup_directory` called from `run_harness_job`'s own `finally`
  block) — not just the "obviously terminal" ones named in an earlier
  draft (pushed successfully, rejected at Gate B, review returned
  `changes_requested`, cancelled), but also the terminal paths the
  test-outcome contract itself introduces: `tests_failed`,
  `test_result_unavailable`, `test_command_not_configured`, and a bare
  runner/container error before any commit was ever made. Every one of
  these is a `finally`-block responsibility on whichever function
  produces that terminal state, not a single catch-all — mirrored
  directly in the test suite, one test per terminal path asserting the
  worktree directory no longer exists afterward. The expiry sweep
  (branch 4's recovery scan) exists only for the case immediate cleanup
  can't cover: a job that never reaches any terminal state at all (a
  crashed worker, an abandoned Gate-B decision) — a backstop, not the
  primary cleanup path.

## Branch table

| # | Branch | Scope |
|---|---|---|
| 1 | `feat/p3-writable-runner` | **done (2026-07-20)** — 10 rounds Codex code-review, the longest implementation arc in this repo's history (exceeding Phase 2 branch 2's 7): R1 2 findings (quiescence loop scanned via `ls`/`grep`, which are themselves external processes — never terminated; test-command failure aborted the whole script under `set -e` before the result file was ever written); R2 2 findings (repo state read before quiescence, so a daemonized child could still mutate the worktree after commit_sha/changed_files were captured; `@{upstream}` has no meaning for a freshly-checked-out branch, silently making changed_files always empty); R3 2 findings (unconfigured push credential fell back to the runner host's own ambient SSH access instead of failing closed; noclobber on the result-file write failed against an agent-planted decoy instead of replacing it); R4 4 findings (dirty worktree wasn't fail-closed against what was actually tested; a mutable base ref could be rewritten by the agent to fake an empty diff; the CLI's own exit code was discarded, letting a crashed CLI report a pre-existing checkout's pass; JSON escaping broke on embedded newlines); R5 2 findings (`GIT_SSH_COMMAND` has zero effect on HTTPS remotes, silently falling back to ambient credentials — fixed by rejecting non-SSH remotes outright; a FIFO/symlink planted at the result path could hang or spoof the host's read — fixed with `lstat`-based rejection); R6 1 finding (post-quiescence `git status` can itself trigger repo-configured hooks like `core.fsmonitor`, spawning a process *after* quiescence believed it was done — fixed with `-c core.fsmonitor=` plus a second quiescence pass right before the trusted write); R7 2 findings (git read failures were silenced instead of failing the result closed; `GIT_SSH_COMMAND`'s key path was unquoted, breaking on paths with spaces); R8 2 findings (an agent could plant a decoy result.json then strip write/execute permission from `/workspace` itself, aborting the wrapper's own write via `set -e` and leaving the decoy as the only content — fixed via `chmod u+rwx` first, safe because the wrapper always owns the directory; the sed-based `json_escape` produced empty output for any single-line command, the common case — rewritten in awk); R9 2 findings (the runner images never installed `git`, so every write-mode run would fail closed with exit 127 even on success; awk's `json_escape` still didn't escape non-newline JSON control characters like tabs/CRs — rewritten to delegate to Node's own `JSON.stringify`, closing the class instead of chasing individual characters); R10 1 finding (the dirty-worktree check itself could be defeated by `git update-index --skip-worktree` or `status.showUntrackedFiles=no`, both agent-controllable via `.git` — fixed by clearing skip-worktree bits and forcing `-c status.showUntrackedFiles=all`). No CRITICAL after plan-review; user checkpointed twice during implementation (after R8, after R10) given the round count, chose "keep iterating" then "accept and move on." All 1627 backend tests, ruff, and `sh -n` pass on every commit. **Original scope (unchanged by the fixes above):** `harness/runner.py`: writable clone + branch checkout, read-write container mount (new job type only), same `--user {host_uid}:{host_gid}` single-uid convention Phase 2 already uses (round-5 fix: no privileged/unprivileged split — see boundary section). The bundle-only clean-clone push primitive (round-3 CRITICAL fix, rounds 4/5/6 tightening): verify `refs/heads/<branch_name>` resolves to `produced_commit_sha`, `git bundle create` that *named ref* (round-6 fix — a bare SHA is rejected by `git bundle create` itself) against the agent's own worktree (a local, non-networked operation — no hook fires for it, no credential helper or SSH override or URL-rewrite rule applies, since none of those are network/remote concerns); fetched into a fresh host-only clone whose `.git/config`/`.git/hooks` the agent never touched; ancestry + SHA re-verified via plumbing; pushed by explicit SHA:refs/heads/branch *from the clean clone* — never `format-patch`/`git am`, never a checkout in the clean clone, and critically, the actual network operation (`push`, where `credential.helper`/`core.sshCommand`/`url.*.insteadOf`/hooks could matter) never runs against the agent's worktree at all. The process-quiesced wrapper result-file contract (round-3 HIGH fix; round-4's uid-separation attempt and round-5's sequencing-only attempt both reverted/tightened at round 6, which found a forked/daemonized grandchild could survive "the subprocess call returned" and race the write — see boundary section): the wrapper forcibly terminates every other process still alive in the container's PID namespace before writing anything, runs the configured `test_command` itself and derives `passed` from its real exit code, computes `commit_sha`/`changed_files` itself via `git` — never trusting CLI-authored stdout/stderr for any of it. Every function here is a standalone primitive taking plain arguments (git URL, paths, branch name, test command string) — no Django model import, testable exactly like Phase 2's `runner.py` was, with mocked subprocess calls and fixture directories (including: a malicious-`pre-push`-hook/malicious-`.git/config` regression test proving the *push* step never runs against the agent's worktree; a detached-grandchild-process regression test proving the quiescence kill wins the race against a result-file write attempt; a spoofed-stdout-JSON regression test; a pre-planted-result-file regression test proving the wrapper's `O_EXCL` write fails closed rather than overwriting tampered content). |
| 2 | `feat/p3-implementation-model-and-gates` | **done (2026-07-20)** — 7 rounds Codex code-review: R1 4 findings (create_execution for both gates unconditionally transitioned status forward regardless of the job's actual current state, so a job cancelled while its gate approval was still pending could be revived by a later approval — fixed with a status re-check; cancelling `pending_review`/`pending_gate_b` only flipped `cancel_requested`, but neither idle-wait phase has a loop watching that flag, so cancellation silently did nothing — both now cancel directly and clean up the worktree; `canonicalize_git_url` stripped `.git` before the trailing slash, missing a real URL spelling and bypassing the self-repo hard-block for it; `capture_logs` ran after the container was already removed, so successful runs silently recorded zero cost); R2 3 findings (`pushing` had no polling loop and never re-checked `cancel_requested` before the actual network push, so a cancelled job could still push — added the one meaningful checkpoint right before it; the writable clone passed an empty `clone_env`, ignoring the configured SSH credential entirely; the cost brake was parsed but never enforced, unlike discovery's identical check); R3 3 findings (`implementing` is both "ready to claim" and "actively running" with no status transition a redelivered Celery task would find already moved past — fenced via `started_at__isnull=True`; the push phase had no claim fence at all — added `push_started_at` + migration; worktree cleanup cleared its DB pointer even when the actual directory removal failed); R4 4 findings, including a P1 (the self-modification hard-block only ever ran once, at job creation — execution code then read `job.repo.git_url` LIVE through an editable FK, so editing a `HarnessRepo`'s remote after Gate A request could silently redirect an already-approved job around the guard — fixed by snapshotting `repo_git_url`/`repo_default_branch` onto the job at creation, migration 0006; push never checked `worktree_expires_at`; the three push finalizers cleared `worktree_path` unconditionally even when cleanup failed; a whitespace-only `test_command` passed a bare truthiness check but ran as a shell no-op); R5 3 findings (`_parse_claude_code_output` used `json.loads`, which rejects the trailing test-command output mixed into the same captured log stream as a parse error, silently zeroing cost and bypassing the whole budget brake — switched to `json.JSONDecoder().raw_decode()`; `_safe_cleanup_directory` trusted a try/except around `shutil.rmtree(..., ignore_errors=True)`, which never raises by design, so cleanup was never actually confirmed — fixed via `os.path.exists()` afterward; the `implementing` → `pending_review` transition never re-checked `cancel_requested` under its own lock, so a cancel landing in a narrow window could still advance to review); R6 2 findings (`test_command` had the identical live-FK snapshot gap `repo_git_url` already closed — added `repo_test_command`, migration 0007; a missing/unparseable CLI result let a run through as a "successful free run" with `estimated_cost` silently at 0 — added the same fail-closed check discovery's own `_finalize` already has); R7 2 findings (`create_execution` for both gates checked only job status, never that the approving `ApprovalRequest` was actually the one linked as `gate_a_request`/`gate_b_request` — since both are real registered tools, an unlinked request created directly through the generic approvals API could later be approved and still transition the job; a non-UUID `job_id` in a malformed payload bubbled out as an unhandled 500 instead of a clean 400). No CRITICAL in any round; P1 only at rounds 1 and 4. User checkpointed twice during implementation (after R4, after R7), chose "keep iterating" then "accept and move on." All 1727 backend tests, ruff, import-linter, and migration checks green on every commit. **Original scope (unchanged by the fixes above):** `HarnessImplementationJob` model + migration (full field list above, including `worktree_path`/`worktree_expires_at`) + `HarnessRepo.test_command` field + migration; self-modification hard-block (`HARNESS_SELF_REPO_IDENTIFIERS` + the canonical normalizer, round-2 MEDIUM fix); `harness_implement`/`harness_push` `ToolSpec` registrations; Gate A + Gate B wiring via `approvals.services.create_request`/`harness/signals.py`, mirroring `agents/execution.py`'s `tool_request` step and `agents/signals.py`'s receiver — including `harness/apps.py`'s own `ready()` hook actually connecting the receiver (round-3 MEDIUM fix, previously missing); service layer (create/list/get/cancel, curator-gated like `HarnessJob`'s); the job's own execution function, now that the model exists, calling branch 1's runner primitives. Review dispatch itself is stubbed/mocked in this branch's own tests (branch 3 wires the real thing) so this branch's state machine can be verified independently. Immediate worktree cleanup on every terminal path this branch's own state machine can reach, including the test-outcome-contract's own failure modes, not only the "obviously terminal" ones (round-2 MEDIUM fix, expanded per round-3 MEDIUM fix). |
| 3 | `feat/p3-cross-vendor-review` | **done (2026-07-21)** — Codex CLI vendor-terms verification resolved GO, API-key auth only (see "Open question resolved: Codex CLI vendor terms" below), then 14 rounds Codex code-review, the longest arc in this repo's history (exceeding branch 1's own 10): R1 2 P1s (mounting the agent's own worktree directly for `git diff` let agent-writable `.git/config`/`.gitattributes` — `diff.external`/`textconv` — hide the real diff from the reviewer or run code inside the review container, fixed by `runner.prepare_review_clone()`: a clean, host-only checkout bundled from the worktree and fetched fresh, mirroring branch 1's own push design; the `jarvis_harness_review` prompt template had no seeding mechanism at all, so every review would fail closed on a fresh deployment — added to `seed_ai_defaults.py`); R2 2 P2s (a lost broker dispatch left the review `pending` and the job `pending_review` forever with no recovery scan yet for this job type — added `resolve_pending_review_after_dispatch_failure`, resolved synchronously rather than waiting on branch 4's future sweep; an exception after the claim committed to `running` left it stuck the same way — added `resolve_running_review_after_unexpected_error`, wired into the Celery task wrapper); R3 2 P1s (the review's own terminal write and the job's consequent transition ran as two separate transactions — a crash between them left a terminal review with the job stuck `pending_review` forever, invisible to the running-only rescue — wrapped every finalize call site in one outer `transaction.atomic()`; a committed `.gitattributes` with `*.py -diff` made a bare `git diff` show "Binary files … differ" instead of the real changed lines, with *zero* `.git/config` involvement — untouched by R1's fix since this is a tracked-file attribute, not a config-driven mechanism, verified with a real repro — `_build_review_goal` now mandates `git diff --text --no-ext-diff --no-textconv`); R4 1 P1 + 1 P2 (Codex CLI — and potentially Claude Code, should it ever join the reviewer pool — automatically discovers and honors `AGENTS.md`/`AGENTS.override.md`/`CLAUDE.md` as trusted project instructions while walking the checkout, confirmed against Codex CLI's own documented discovery behavior, letting a malicious implementation commit instruct its own reviewer to approve regardless of content — added `_strip_agent_instruction_files()`, removing every match from the clean clone before the container ever launches, failing closed if removal can't be confirmed; a leaked review container on `docker rm` failure has no recovery sweep the way `HarnessJob` does — explicitly deferred to branch 4's own recovery-scan extension as a documented, accepted residual risk rather than fixed here); R5 2 P2s (`_claim_review` only checked the review row's own status, not the job's live status, so a job cancelled after the review was queued but before claimed could still have its review launch and spend credentials — added a job-status check under the same lock; head-only transcript truncation could silently drop the verdict `_build_review_goal` asks the CLI to place at the *end* of the transcript, unlike discovery's own front-loaded transcript — switched to a head+tail window); R6 1 P2 (`CODEX_API_KEY` was undocumented in `.env.example` despite the repo's own convention — added it); R7 1 P1 + 1 P2, **user checkpoint** — "fix both, keep iterating" (filename matching for the R4 stripping guard was exact-case, so a committed `agents.md`/`claude.md` survived on a case-insensitive filesystem — the review CLI would still discover it there — fixed via case-insensitive comparison; the review clean-clone's checkout could trigger a host-configured git filter driver — Git LFS being the common case — named in the reviewed commit's own `.gitattributes`, since global/system git config wasn't isolated from that checkout — added `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null` isolation, threaded through every git call `prepare_review_clone` makes after the initial clone); R8 1 P2 (a container that timed out or exited non-zero could still have emitted real diagnostic logs, but this path discarded them, leaving `raw_transcript` empty for common `review_unavailable` causes like a missing credential or a mid-run crash — now bounded and preserved); R9 1 P2 (the dispatch-failure resolver had the identical non-atomic review/job transition R3 already fixed elsewhere, missed because this function predates that fix — wrapped in one transaction, mirroring R3's pattern); R10 1 P2 (the ancestor-check `merge-base` call's own timeout bypassed the function's `except RunnerError` cleanup entirely, leaking the full clean-clone checkout in `/tmp` — wrapped to convert `TimeoutExpired` into `RunnerError`); R11 1 P1, **user checkpoint** — "fix narrowly, keep iterating" (confirmed empirically by directly running `codex exec` and inspecting its two output streams: Codex CLI's actual transcript — banner, prompt, reasoning/tool output — goes to stderr, with only the bare final-answer line on stdout; `runner.capture_logs()` only ever returns stdout, and Codex CLI is the *only* selectable review agent, so a review's `raw_output` was just that one bare line — worse than failing closed, since it usually isn't literally empty, so the gateway would summarize a verdict from almost no real context — fixed by redirecting stderr into stdout inside `codex_cli_run.sh`'s read-only invocation, the narrowest fix available since it doesn't touch `capture_logs()` itself or Claude Code's own `--output-format json` wrapper); R12 1 P2 (`AI_FAKE_PROVIDER=true`, the repo's dev/example default, had no fixture for the new `harness.review` gateway purpose, so every review failed closed as `review_unavailable` in that mode — added `FAKE_HARNESS_REVIEW_RESPONSE`, mirroring `harness.briefing`'s existing fixture); R13 1 P2 (the transcript was only persisted *after* the gateway call succeeded, so a soft-time-limit/crash interruption during that call lost the only captured transcript for exactly the cases an operator most needs to diagnose — persisted before the call now, mirroring discovery's own identical round-5 fix); R14 2 P2s, **the final round** — user capped the loop at 13 rounds ("run one more round, then stop regardless" given the round count had already exceeded branch 1's own record and recent findings were trending marginal) — fixed since both were cheap and legitimate, just without triggering a round 15 (a malformed `findings` value silently became `[]` while the surrounding `summary`/`verdict` were still trusted, letting an approval proceed despite the response not matching its own schema — now fails the whole response closed; `resolve_commit_sha`'s bare `subprocess.run()` let a timeout bypass both of its callers' cleanup wrappers — fixed once at the function's own level, closing the identical gap in branch 1's already-merged `push_bundled_commit` too). No CRITICAL in any round; P1 at rounds 1, 3, 4, 7, and 11. User checkpointed four times (after R3, R7, R11, and the R13→R14 round-count checkpoint), choosing "keep iterating" three times and a capped final round the fourth. All 1787 backend tests, ruff, import-linter, and migration checks green on every commit. Three narrowly-scoped follow-ups flagged (not fixed here, each spawned as a tracked background task): documenting the rest of the harness's own `HARNESS_*` env vars in `.env.example` (only `CODEX_API_KEY` was added here); the identical `merge-base`-timeout clean-clone leak in branch 1's already-merged `push_bundled_commit` (R10 fixed `prepare_review_clone`'s own instance; `resolve_commit_sha`'s twin gap was closed for both by R14, but `push_bundled_commit`'s *own* separate `merge-base` call still has it); redirecting stderr in `codex_cli_run.sh`'s write-mode invocation too (same root cause as R11, but dormant since Codex CLI isn't a selectable implementation agent yet). **Original scope (unchanged by the fixes above):** `HarnessReview` model + migration; the review container run (read-only, its own narrowly-scoped execution function per the boundary section — not the discovery job's engine, not a shared one); the gateway-summarization pipeline for `HarnessReview.summary/findings/raw_transcript` (reuses branch 3 Phase 2's fencing functions directly); the mechanical `reviewer.agent != implementer.agent` enforcement; the split `_SELECTABLE_IMPLEMENTATION_AGENTS`/`_SELECTABLE_REVIEW_AGENTS` sets. |
| 4 | `feat/p3-implementation-pipeline` | **done (2026-07-22)** — 7 rounds Codex code-review. Scoping first: reading the current code showed branches 1–3 had already fully wired the real end-to-end flow (Gate A → implement → dispatch review → Gate B → push) incidentally while landing their own scope, so this branch's actual remaining work was narrower than originally planned — the recovery-scan extension and the kill-switch's approval-revoke, both explicitly deferred by earlier branches. R1 2 P2s, **user checkpoint** — a real, empirically-confirmed deadlock: `cancel_harness_implementation_job` revoking a gate-linked `ApprovalRequest` could deadlock against a concurrent `approve_request` on the same request. A two-connection concurrency test kept failing even after moving the revoke call outside a nested `transaction.atomic()` block — the actual root cause took real investigation (Postgres server-log inspection, `connection.in_atomic_block` tracing) to find: the function carried a pre-existing `@transaction.atomic` FUNCTION decorator, making the "inner" block a mere savepoint — the job's row lock was never actually released until the whole function returned, i.e. after the revoke calls, reproducing the identical deadlock. Fixed by removing the redundant decorator; a residual indirect race (the revoke's own signal handler scheduling another job-lock attempt) was narrowed with a non-locking pre-check and closed by making each revoke call best-effort (also discovered and reverted a previously-spawned follow-up task based on a false premise from this same investigation — `cancel_harness_job`'s own decorator had simply been missed on an earlier read). Also fixed: review recovery's redispatch reused the original dispatch's fail-closed handler, converting a transient broker outage into a permanent failure on the first retry pass — added a genuinely retry-only redispatch variant. R2 1 P2 (`job.worktree_path` only ever persisted on the success path, so a real hard-crash left the recovery scan's own worker-crashed case with no way to find the actual on-disk directory — persisted immediately once the clone succeeds). R3 2 P2s (the round-2 fix's own regression: `_fail_and_cleanup` cleaned up the directory but never cleared the now-stale DB pointer on every non-success terminal path — fixed via the same confirmed-cleanup discipline the push finalizers already use; a genuinely-failed worktree cleanup at finalize time left a terminal job with no future sweep ever revisiting it — added a leaked-terminal-worktree case mirroring discovery's own leaked-container sweep). R4 1 P2, a real correctness bug: the recovery scan read a stale job unlocked, tore down its container and deleted its worktree, and only THEN did the real, locked status check — a live worker genuinely finishing at that exact moment (about to succeed) could have its still-needed worktree destroyed before the check ever ran. Fixed by claiming (atomic re-check + transition) strictly before any cleanup, mirroring discovery's own `_claim_stale_job` discipline, and proactively applied to the analogous pushing case too. R5 1 P2 + 1 P3 (the leaked-worktree sweep had no sibling case for `container_id`, leaking credential-bearing stopped containers indefinitely — added; the recovery path's own audit action said "failed" even for a job actually recovered as "cancelled" — fixed to match `job.status`). R6 1 P2 (the worktree-retention sweep's revoke call attributed a passive system timeout to the job's own creator, misrepresenting it as an explicit human decision in the approvals audit trail — made the revoke functions' `actor` optional, defaulting to `SYSTEM` attribution, mirroring `record_expired`'s existing convention; the direct cancel-triggered call site is unaffected). R7 clean. No CRITICAL in any round; P1-equivalent severity only at the round-1 deadlock, which is why it was the only round checkpointed. All 1833 backend tests, ruff, import-linter, and migration checks green on every commit. **Original scope, delivered as scoped down by the branch's own investigation:** `approvals.services.revoke_request` (new, reuses the existing `expired` terminal state and its `post_save` signal machinery rather than inventing a new status or job-transition path); `cancel_harness_implementation_job` extended to revoke `gate_a_request`/`gate_b_request` on cancel; `recover_stale_harness_implementation_jobs` (new) covering both a lost-gate-approval-dispatch case and a worker-crashed case for `implementing`/`pushing`, a worktree-retention backstop sweep (deliberately scoped to `pending_gate_b` only, not `pending_review`, since an in-flight review may still be actively bundling from that same worktree), and leaked-terminal-worktree/leaked-terminal-container sweeps; `recover_stale_harness_reviews` (new) closing branch 3 round 4's explicitly-deferred leaked-review-container gap plus a lost-dispatch/hard-crash backstop; two new Celery beat entries. |
| 5 | `feat/p3-harness-hardening` | **re-planned 2026-09-27.** (a) Land the three branch-3 follow-ups that sat uncommitted: `push_bundled_commit` merge-base timeout → `RunnerError`; `codex_cli_run.sh` write-mode `2>&1` (no Claude-side counterpart — its JSON result must stay alone on stdout); the `HARNESS_*` `.env.example` block **with every line commented out showing its default** (an uncommented `KEY=` parses as `""` and crashes `env.int` at boot). (b) Close the 2026-09-27 audit's harness findings: wrapper owns the commit (`git add -A` + hooks-disabled commit after the CLI exits; a no-change run fails early as `tests_failed`/no-changes rather than travelling to push); the dirty-tree check clears assume-unchanged *and* skip-worktree for every flag letter via NUL-separated `ls-files -v -z`, runs `update-index --really-refresh`, neutralizes `.git/info/exclude` and `core.excludesFile`, forces `core.quotePath=true`; the test command runs with the CLI API keys removed from its environment; `result.json` decode errors and raw `TimeoutExpired`/`OSError` from `capture_logs`/`container_is_running`/`container_exit_code` become `RunnerError` (exit-code helper returns `None`), so jobs finalize immediately with their real cost; cost parsing only trusts a wrapper-emitted, post-quiescence marked copy of the CLI's own stdout (last marker wins); host cleanup `rmtree` restores owner write permission on failure and retries; containers run with `--cap-drop=ALL` and `--security-opt=no-new-privileges`; the `pending_review`-with-no-review-row stuck state is closed (review row created in the same transaction as the transition, dispatch on commit) plus a recovery case for legacy rows; cancelling an implementation job tears down an in-flight review container; self-repo canonicalization collapses repeated/leading path slashes, strips a trailing host dot, maps `ssh.github.com` → `github.com`, and implementation-job creation **fails closed when `HARNESS_SELF_REPO_IDENTIFIERS` is empty** (an ADR-004 hard rule must not relax by absence of config). (c) Record the O-3 decision (below). |
| 6 | `feat/p3-implementation-api` | **new 2026-09-27** (the original branch 5 assumed an implementation-job API that was never built). `GET/POST harness/implementation-jobs`, `GET harness/implementation-jobs/<id>` (nested reviews; gate A/B projected as `{id, status, decided_at}` only — never `payload`), `POST …/<id>/cancel`; role × endpoint + isolation tests. `HarnessImplementationJob.raw_transcript` (new field) stores the implementation CLI's own output, fenced and labeled like discovery's. `diff_summary` becomes gateway-produced (purpose `harness.diff_summary`, over the fenced diff stat + file list, deterministic fallback on gateway failure, ModelUsage recorded) as the boundary section always intended. A safe, server-built approval display summary (`display_summary` on `ApprovalRequestSerializer`, per-`ToolSpec` `describe_request` hook) so Gate A shows goal/repo and Gate B shows branch, changed files, diff summary, test result and review verdict — raw `payload` stays unexposed. `work_log` entries for implementation and push outcomes (ADR-008 decision 4). `ToolSpec.user_invocable` (default true; false for `harness_implement`/`harness_push`/internal tools) filters the router and planner tool vocabulary. |
| 7 | `feat/p3-harness-ui` | **re-scoped 2026-09-27.** Extend `/harness`: repo picker (BFF for the existing repos API) instead of a raw UUID; request an implementation (fresh, or from a discovery job's detail view); pipeline view over the 8 statuses with Turkish labels for all 14 failure reasons; both gates' status with deep links into `/approvals?request=<id>` (highlight + scroll); review verdict + findings + diff summary, with **two** independently fenced raw transcripts behind disclosures (implementation, review) and the R2 labeling discipline; reviewer shown (not chosen) — implementation is Claude Code, review is Codex CLI. Approvals: per-tool card rendering (vault export vs `harness_implement`/`harness_push`), display summaries, harness execution statuses, polling while a pipeline is live. Frontend "fix now" items from the audit: font token + `latin-ext`, shell height/overflow chain, chat error surfaces and post-stream flash, `HarnessJobDetail` skeleton after a load error, keyboard-reachable job/agent cards, Playwright smoke selectors updated to the Turkish UI. |
| 8 | closeout | **new 2026-09-27.** A real-container smoke test (real Docker launch of the built images with a stubbed CLI binary — exercises mounts, network, result contract and quiescence without paid API calls), image build + digest pinning instructions, an owner runbook for the first paid end-to-end run; CI green on the integration PR (akin941/jarvis-os#26) and its merge to `main`; phase closeout notes. |

## Out of scope (explicit)

Deploy automation / VPS deploy / merge to `main` (Phase 5) · a
general-purpose test-runner abstraction across arbitrary repo tooling
(the CLI agent's own responsibility, per boundary section — Jarvis only
defines the wrapper-owned JSON *result* schema, never the test command
itself) · Gemini CLI / Ollama as implementation or review agents
(follow-up once a real job-distribution policy table exists, same
deferral as Phase 2) · a third+ review round or iterative
revise-per-feedback loop (a `changes_requested` verdict fails the
implementation job outright in this phase; the curator re-requests a
fresh implementation with updated guidance — a tighter "same job, second
attempt" loop is a later-phase refinement) · SSE live log streaming
(still deferred from Phase 2, revisit once these longer 10-60 min
implementation jobs make polling genuinely painful — R4 below) · any
change to `Workspace.profile` dossier schema · merging `agents` (MVP 7)
and `harness` (O-3's harness-side scope — explicitly still deferred to
*after* this phase per VISION §2's own inventory table, not resolved
here even though this is "post-Phase-3") · extracting a shared
execution engine across `HarnessJob`/`HarnessImplementationJob`/
`HarnessReview` (explicitly deferred, boundary section) · a repo-level
configurable branch-name prefix (O-3, deferred until a real repo needs
it).

## Test strategy

Same discipline as Phase 2 (CLAUDE.md rule 7, rule 5): role × endpoint
permission tests for every new surface; the CLI invocation and Docker
subprocess layer stay fully mockable, no test depends on a real
`claude`/`codex` binary or a real `git push`; migration check; `make
lint` + `make test` green per branch. New scenarios this phase adds:
- The Gate-A/Gate-B state machine: every legal and illegal transition,
  including both recovery-scan cases and the worktree-expiry case.
- The self-modification guard: a `HarnessRepo` matching
  `HARNESS_SELF_REPO_IDENTIFIERS` is rejected by
  `create_harness_implementation_job()` before any `ApprovalRequest`
  exists — direct unit test, not inferred from the setting's presence.
- The cross-vendor enforcement: a `HarnessReview` whose `agent` equals
  its `HarnessImplementationJob.agent` must be rejected at the service
  layer — direct unit test, mirroring the cross-workspace
  `HarnessJob.save()` invariant test's shape from Phase 2.
- The read-only vs read-write container-mount split: a source-level or
  argv-assertion test proving `HarnessJob`/`HarnessReview` never get a
  writable mount and `HarnessImplementationJob` never gets a read-only
  one — a real security property, not incidental.
- The wrapper-owned test-result contract: missing/malformed JSON, and
  `tests.ran=false`, both fail closed with no Gate B request and no
  review dispatch — direct tests for each shape, not just the happy path.
- Kill switch while blocked on either approval gate: cancel must not
  leave an orphaned `ApprovalRequest` a later approval click could act on
  for an already-cancelled job.
- A `changes_requested` review verdict never reaches Gate B — direct
  test, not just inferred from the state machine's shape.
- The credential-resolution path for push: never logged, never present
  in `HarnessImplementationJob`'s own persisted fields, mirrors branch 2
  Phase 2's `_resolve_cli_env` test discipline exactly.
- Worktree-retention-deadline sweep: an unresolved job past
  `HARNESS_WORKTREE_RETENTION_HOURS` is failed and its worktree cleaned
  up by the recovery scan, not left orphaned on disk indefinitely.

## Risks

- **R1 — Write authority, for real this time.** This is the phase VISION
  §8's R1 explicitly named as "the riskiest permission": repo write
  (deploy is still Phase 5, but a pushed branch is a real external-facing
  change). Mitigations: two independent approval gates (not one),
  worktree isolation (never `main`, never a branch a human might be
  using), the self-modification hard-block, cross-vendor review as a
  mechanical enforcement (not a convention), a bundle-only push (named
  ref, verified SHA) from a host-only clean clone that never touches
  agent-controlled `.git` state at all (round-3 CRITICAL fix, rounds 4/6
  tightening), the same container/network/resource brake system Phase 2
  already proved, a process-quiesced, wrapper-derived (never
  agent-reported) fail-closed test-outcome contract.
- **R2 — Prompt injection, now with two more surfaces.** Branch 3/4 Phase
  2 solved this for the discovery briefing; Phase 3 has the same problem
  twice more — the implementing agent's own account of what it changed
  (`diff_summary`), and the reviewing agent's own account of the diff —
  both untrusted-content-in scenarios needing the identical fencing
  discipline, not a new one invented per surface.
- **R3 — Cross-vendor review is blocked on an external dependency (Codex
  CLI vendor terms, O-2 continued from Phase 2). RESOLVED, 2026-07-21:
  GO, API-key auth only** — see "Open question resolved: Codex CLI
  vendor terms" below for the full grounded-evidence writeup. The
  API-key path ADR-008 decision 7 anticipated as a fallback turned out
  to be the only clearly-compliant path outright, not a contingency for
  if terms blocked automation generally.
- **R4 — Longer jobs.** Implementation jobs (worktree write + test run)
  run longer than discovery's single-shot CLI call; still poll-based per
  this brief's own scope decision, revisited if real usage makes it
  painful.
- **R5 — Duplicated brake-system shape across three job types.** Accepted
  explicitly (boundary section) rather than risking a premature
  abstraction; revisit once all three job types' real differences are
  known from having actually shipped, not guessed at design time.
- **R6 — `agents`/`harness` merge evaluation lands mid-roadmap.** O-3 says
  "re-evaluated after Phase 3" — this phase does not resolve it, but by
  the time it ships, `harness` will have its own approval-gate
  integration that looks structurally very close to `agents`' own
  `tool_request` step (both now go through `tools.registry` +
  `approvals.services.create_request`). Worth flagging now so whoever
  picks up O-3 later isn't surprised by how much closer the two runtimes
  have become by construction, not by design intent.
- **R7 (new, round-1 finding) — worktree persistence across process
  restarts.** A worker crash between implementation completing and Gate
  B being decided must not lose the finished worktree or leave
  `HarnessImplementationJob` stuck with no recovery path — the
  `worktree_path`/`worktree_expires_at` fields and branch-4's recovery
  scan extension exist specifically to close this gap, not as
  observability nice-to-haves.

## Open question resolved: Codex CLI vendor terms (R3, O-2 continued from Phase 2)

**GO — API-key authentication only, never "Sign in with ChatGPT."** Resolved
2026-07-21, branch 3's first concrete task, same grounded-evidence
discipline Phase 2 branch 2 used for Claude Code's own headless-mode
verification (that brief's "Open question resolved: O-2").

- **The two OpenAI terms documents are genuinely separate, and each
  says so explicitly.** The consumer **Terms of Use**
  (`openai.com/policies/row-terms-of-use/`, effective 2026-01-01) governs
  "ChatGPT, DALL-E, and OpenAI's other services for individuals" and
  states directly, in its own text: *"İş Koşullarımız, ChatGPT
  Enterprise, API'lerimiz ve işletmeler ile geliştiricilere sunduğumuz
  diğer hizmetlerin kullanımını düzenler"* ("Our Business Terms govern
  the use of ChatGPT Enterprise, our APIs, and other services we provide
  to businesses and developers") — the API is explicitly carved out into
  a different document from the start. The separate **OpenAI Services
  Agreement** (`openai.com/policies/business-terms/`, effective
  2026-01-01) states its own scope just as directly: *"yalnızca
  işletmeler ve geliştiriciler için OpenAI'ın API'leri... kullanımına
  uygulanır"* ("applies only to the use of OpenAI's APIs... for
  businesses and developers").
- **The "no automatic/programmatic use" restriction lives ONLY in the
  consumer Terms of Use, not the Business/API terms.** The consumer ToU's
  restriction list includes *"Otomatik veya programlı olarak veri veya
  Çıktıyı dışa aktarma"* ("automatically or programmatically
  exporting/extracting data or Output") — this is the clause that
  initially looked concerning. It does not appear anywhere in the
  Business/API Services Agreement's own restriction list (§3.3,
  items a-i): that list instead covers illegal use, IP/reverse
  engineering, minors, training competing models, extracting data
  *"hizmetler aracılığıyla izin verilenler dışında"* ("outside of what's
  permitted THROUGH the services" — i.e. circumventing the API, not
  using it), API-key resale, and rate-limit evasion. Using the API as
  designed — including running it unattended/headless — is the
  sanctioned mechanism under this document, not a restricted one.
- **Codex CLI's own official docs explicitly design for and document
  unattended/automated use under an API key.** `learn.chatgpt.com/docs/
  non-interactive-mode` documents `codex exec` for scripts and CI/CD
  pipelines by name, a dedicated `CODEX_API_KEY` env var with explicit
  least-privilege guidance ("set it only for the single `codex exec`
  invocation"), `--sandbox`/`--ephemeral`/`--json`/`--output-schema`
  flags built for pipeline use, and an official `openai/codex-action`
  GitHub Action wrapper. This is not a workaround being stretched to fit
  an unintended use case — it is the documented, supported path.
- **"Sign in with ChatGPT" (ChatGPT-subscription OAuth) is a genuinely
  unresolved gray area, and must NOT be used for this harness.**
  `github.com/openai/codex/discussions/8338` asked OpenAI directly
  whether automated/commercial use under ChatGPT-subscription auth (as
  opposed to an API key) is permitted; the OpenAI maintainer who replied
  declined to give a definitive answer, said explicitly they are "an
  engineer, not a lawyer," and pointed back to the general consumer
  Terms of Use without resolving which document actually governs that
  auth path. No later comment in the thread resolved it either. This
  auth method sits in exactly the ambiguity the consumer ToU's
  restriction was written for (an individual's ChatGPT
  account/subscription, not a business API relationship) — automating
  under it would not be "verified, grounded evidence," it would be an
  unverified bet.
- **A code-review verdict is not an automated high-stakes decision under
  either document's Usage Policies.** OpenAI's Usage Policies restrict
  automated decisions in named sensitive domains (law enforcement,
  migration, critical infrastructure, safety components, credit,
  employment, housing, education, insurance, and similar) — a
  `HarnessReview` verdict is none of these, and by this phase's own
  design (brief §boundary) it is advisory input to Gate B, never itself
  the push decision: a human curator still makes that call.
- **Consequence for this branch's design:** `_SELECTABLE_REVIEW_AGENTS`
  (brief §boundary) adds `CODEX_CLI` on this branch, but the runner only
  ever resolves Codex CLI credentials from `CODEX_API_KEY`-shaped env
  (mirrors `harness.execution._resolve_cli_env`'s existing one-credential-
  per-capability shape) — never from a stored ChatGPT session/OAuth
  token, and never by shelling out to whatever interactive auth state
  happens to be on the runner host (the exact ambient-credential
  fail-open pattern branch 1's own `_resolve_git_push_env` already
  guards against for git push, applied here to the review agent's own
  auth instead). The API-key fallback ADR-008 decision 7 anticipated
  turns out to be the ONLY clearly-compliant path, not a contingency for
  if terms block automation — terms don't block API-key automation, they
  only leave the OAuth path unresolved, so that path is simply never
  built.

Sources: [OpenAI Terms of Use](https://openai.com/policies/row-terms-of-use/) ·
[OpenAI Services Agreement](https://openai.com/policies/business-terms/) ·
[Codex CLI non-interactive mode docs](https://learn.chatgpt.com/docs/non-interactive-mode) ·
[GitHub discussion openai/codex#8338](https://github.com/openai/codex/discussions/8338)

## Open questions (updated after plan-review round 2)

- ~~O-1~~ **resolved (plan-review round 1):** both gates are real
  `ToolSpec`s. See boundary section.
- ~~O-2~~ **resolved (plan-review round 1):** discovery-job FK stays
  optional. See boundary section.
- ~~O-3~~ **resolved (plan-review round 1):** `harness/p3-<job-id>` is
  sufficient; fail closed on a pre-existing remote branch, never force-push.
- ~~O-4~~ **resolved (plan-review round 2), superseded by the round-2
  fix itself:** vendor-verifying "can the CLI emit structured test
  output" was the wrong question — the wrapper runs the configured
  `test_command` as its own subprocess and derives the result from its
  own exit code, never from anything the CLI agent reports. No vendor
  behavior to verify for test truth specifically (Codex CLI vendor-terms
  verification in branch 3 is still needed, but for automated-use terms
  generally, not this).
- ~~O-5~~ **resolved (plan-review round 2):** a dedicated canonical
  normalizer (lowercased `host/owner/repo`, `.git`/trailing-slash
  stripped, scp-like and `https://` forms both resolved to the same
  canonical string), not exact string match and not a reuse of
  `harness/serializers.py`'s credential-rejection parsing. See boundary
  section.

No open questions remain from the plan-review rounds.

## Closeout re-plan 2026-09-27

After a two-month pause a full audit (MASTER_PLAN §AA "Plan audit
2026-09-27") re-read this brief against the shipped code. Branches 1–4
hold up — the gates, fencing and push design were confirmed sound — but
the audit found (1) the original branch 5 assumed a
`HarnessImplementationJob` detail endpoint and an implementation
transcript that were never built, (2) the boundary section's
gateway-produced `diff_summary` never landed (branch 2's placeholder
survived), and (3) a set of residual harness robustness/security gaps
listed in branch 5 above. The closeout is therefore four branches (5–8)
instead of one.

**O-3 decision (VISION §8, due "after Phase 3"): keep `agents` and
`harness` separate.** They already share the kernel where it matters —
both create `ApprovalRequest`s through `tools.registry`, both report
usage through `ai_core` — while their execution models differ in kind:
`agents` runs short in-process gateway plans born from chat, `harness`
runs long, containerized external CLIs with repo-write authority and
three owner gates. Merging them would put repo-write machinery behind
the chat planner's looser brakes. Re-evaluate only if a chat-born plan
needs repo-write authority; until then the assistant hands such
requests to the harness UI instead.
