export const meta = {
  name: 'friday-swarm-p13-home-hub',
  description: 'Phase 13 home hub (ADR-016, home server runbook, multi-node, Linux node, Home Assistant media control): brief, Sonnet review loop per branch, serialized merge+test+push',
  phases: [
    { title: 'Implement', detail: 'brief writer, then one worktree agent per branch' },
    { title: 'Review', detail: 'independent Sonnet reviewer until APPROVE (max 3 rounds)' },
    { title: 'Merge', detail: 'serialized merge into integration branch, full lint/test, push' },
  ],
}

const INTEG = 'integ/phase-13'
const REPO = '/home/user/f.r.i.d.a.y.'
const ENV = 'export DATABASE_URL=postgres://jarvis:jarvis@localhost:5432/jarvis REDIS_URL=redis://localhost:6379/0'
const TRAILER = 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01XEgeLKAHARtsKMgFxNJjAa'
const PROPOSAL = 'docs/research/2026-11-roadmap-proposal.md'

const MAC = `PLATFORM BOUNDARY (owner decision 2026-11-20): this container is Linux without macOS/Xcode. Everything buildable and testable here (Django apps, WS consumers, protocol schemas, signing/verification, frontend HUD panels, Python components such as a Pipecat pipeline with mocked audio, test doubles) is IN SCOPE and gets full branches now. Anything that must be built, signed or run on macOS (the Swift menu-bar app friday-node, Keychain, Touch ID, TCC grants, AppleScript/AX, notarization, real microphone/AEC, real hardware measurements) is NOT implemented here: put it in a final brief section "Mac session (deferred)" as a precise, self-contained task list for a later Claude Code session on the owner's MacBook (files to create, protocol it must satisfy, the server-side contract tests it must pass), and mark such rows in the branch table "deferred: Mac session". The server side must be exercised against a Python reference node / protocol test double implementing the node side of the protocol, so the compromised-server/contract tests exist now as the conformance suite the Swift app must later pass. Go/no-go checkpoints that need real-world owner data or the Mac are recorded as "pending owner evaluation" and do not block the next phase.`

const PHASES = [
  { key: 'p13', dir: 'phase-13-home-hub', title: 'Phase 13 — Home hub (home server, many nodes, one Friday)', extra: `NEW PHASE (owner decision 2026-11-26). Write the full brief at docs/prompts/phase-13-home-hub/brief.md (create the directory) AND, in the same docs branch: (1) docs/adr/ADR-016-home-hub-topology.md (status Accepted 2026-11-26): one always-on Friday server on a HOME PC (Linux, Docker Compose stack: Django, Postgres+pgvector, Redis, Celery, node-gateway) on the home LAN; Friday is used only at home; every PC (Macs now, Linux/Windows later) is a paired node of that one server so memory/identity are the same on every PC; reach = LAN only by default, Tailscale optional; node WSS still TLS with a pinned server cert/private CA (ADR-013 unchanged); full-disk encryption on the home server; nightly encrypted pg_dump + restic/age to an off-site target (Cloudflare R2 optional) with a tested restore; an explicit later migration path to a VPS (same Compose stack, restore from backup, re-point nodes) without changing the node protocol; rejected: Supabase (only hosted Postgres, splits stack, third party), object storage as DB, server-on-one-Mac. (2) docs/runbooks/home-server-setup.md: concrete steps for a Linux mini PC (Ubuntu LTS, LUKS, static IP/DHCP reservation, mDNS name, Docker Compose, TLS for the node gateway via a private CA, firewall LAN-only, backups + restore drill, upgrade, migration-to-VPS checklist). (3) MASTER_PLAN §AA Friday 3.0 table: add row "13 Home hub" with execution order right after the Phase 9 Mac session (the node must exist), plus §O decision d11 recording this owner decision. Phase 13 scope (server side here; Mac/Windows parts deferred): MULTI-NODE as a first-class setup (Phase 9 left it "allowed but not optimised"): node naming/roles/rooms, per-node presence in the HUD, "which PC" resolution for voice/chat requests (explicit name, last-active, ask when ambiguous), per-node capability catalogues, per-node audit; invocations always target exactly one node, never broadcast writes; ADR-014 classes unchanged (control actions still gated per node). A LINUX NODE built from the existing Python reference node (backend/nodes/reference, integrations/node_reference) productionised as a separate package with read capabilities + the same conformance suite (Windows node = stub/deferred). SMART HOME via the existing Home Assistant bridge (Phase 5 branch 7, read connectors/manifests + docs/mcp): media/TV control (media_player power, volume, source, play/pause) and scene/light control as allowlisted HA service calls, writes behind the approval gate or confirm-class per ADR-009/014 with explicit owner-configured allowlist of entities; voice routes ("salondaki TV'yi aç") that only propose (ADR-015). Home-only: no public exposure; preflight checks for the home server (LAN reachability, TLS pin, backup freshness, restore drill age). Ground every branch in the real code. Branch table 4-8 server-side branches + closeout; Mac/Windows-only parts go to "Mac session (deferred)" and must also be appended to docs/runbooks/mac-session-handoff.md as a new part after the Phase 12 items.` },
]

const NODES = {}
const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    branch: { type: 'string' },
    head_sha: { type: 'string' },
    summary: { type: 'string' },
    tests: { type: 'string', description: 'exact lint/test commands run and their summary lines' },
    owner_steps: { type: 'array', items: { type: 'string' }, description: 'steps that need the owner machine/credentials, recorded in the brief' },
    ok: { type: 'boolean', description: 'true only if lint + tests are green and the branch is committed' },
  },
  required: ['branch', 'head_sha', 'summary', 'tests', 'owner_steps', 'ok'],
}
const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['APPROVE', 'REVISE'] },
    findings: { type: 'array', items: { type: 'object', properties: {
      severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] },
      file: { type: 'string' }, issue: { type: 'string' }, fix: { type: 'string' } },
      required: ['severity', 'issue'] } },
  },
  required: ['verdict', 'findings'],
}
const MERGE_SCHEMA = {
  type: 'object',
  properties: { ok: { type: 'boolean' }, merge_sha: { type: 'string' }, notes: { type: 'string' } },
  required: ['ok', 'merge_sha', 'notes'],
}

const COMMON = (id, n) => `You are one agent in a swarm building the Friday (formerly Jarvis) personal AI OS repo (${REPO}). Integration branch: \`${INTEG}\` (a LOCAL branch; it always holds every already-merged dependency). Never push anything (the merger alone pushes the integration branch); never commit to main or to \`${INTEG}\` yourself.
Environment: Postgres(+pgvector) and Redis already run locally. Always \`${ENV}\` and set a UNIQUE test DB: \`export DJANGO_TEST_DATABASE_NAME=test_${id}\` (other agents run pytest concurrently). Backend: \`cd backend && uv sync --frozen\` then \`uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run python manage.py makemigrations --check --dry-run && uv run pytest -q\`. Frontend (if touched): \`cd frontend && npm ci && npm run lint && npm run typecheck && npm test\`. The machine has 4 CPUs; while iterating run targeted tests, but run the FULL backend suite once before you finish. The full backend suite takes ~20 min, longer than the 10-min foreground Bash limit: run it in the background (\`uv run pytest -q > /tmp/<your-id>-pytest.log 2>&1; echo EXIT=\$? >> /tmp/<your-id>-pytest.log\`) and wait for it with the Monitor tool / run_in_background, then read the tail. COMMIT YOUR WORK EARLY AND OFTEN (before any long test run) so nothing is lost if you are stopped. When you finish, run \`git checkout --detach\` in your worktree so the branch is not held checked out (other agents must be able to check it out).
Rules that bind (CLAUDE.md): workspace scoping via for_actor(); all model calls through ai_core's InferenceGateway with ModelUsage; approval gate for any external write/deploy/send; LLM code mockable; import-linter one-way deps; tests for every feature incl. role x endpoint; no real LLM/MCP/GitHub/Google/HA/VPS in tests. Conventional Commits, each commit message ending with:\n${TRAILER}
Spec: the section "${n.section}" of \`${n.brief}\` (read the whole brief: decisions, test strategy, risks; and ADRs it cites). Anything needing the owner's Mac, real credentials, paid calls or real hardware cannot happen here: implement with seams/mocks and record it in the brief under a dated "Owner steps (pending)" list for this branch rather than skipping work. Keep the brief's branch table/status updated for this branch (status: implemented, date 2026-11-20).`

async function implement(id, n) {
  return agent(`${COMMON(id, n)}

TASK: implement branch \`${n.branch}\`. You are in a fresh git worktree. First: \`git fetch -q . 2>/dev/null; git checkout -B ${n.branch} ${INTEG}\` (base it on the LOCAL integration branch, which already contains its dependencies). Implement the full scope, write the tests, make lint+tests green, commit (several commits fine). Return the structured result; head_sha = \`git rev-parse ${n.branch}\`.`,
    { label: `impl:${n.branch}`, phase: 'Implement', schema: IMPL_SCHEMA, isolation: 'worktree' })
}

async function review(id, n, round) {
  return agent(`You are an independent code reviewer (the implementer is a different model; you never edit code). Repo ${REPO}. Review branch \`${n.branch}\` against integration branch \`${INTEG}\`: use \`git -C ${REPO} diff ${INTEG}...${n.branch}\` and \`git -C ${REPO} show ${n.branch}:<path>\` to read files (do NOT checkout anything, do not modify the working tree). Spec: section "${n.section}" of \`${n.brief}\` plus CLAUDE.md non-negotiable rules and the ADRs the brief cites. Review round ${round}.
Check: spec coverage (every Scope/Tests/Done-when item done or explicitly recorded as an owner step), correctness bugs, security (approval-gate bypass, credential leakage, workspace leakage, injection, SSRF, sandbox escape), gateway/ModelUsage rule, import contracts, missing tests (role x endpoint), migrations sanity. You may run read-only commands, and you may run tests in a temporary worktree (\`git -C ${REPO} worktree add /tmp/rev-${id}-${round} ${n.branch}\` then remove it) with \`${ENV}; export DJANGO_TEST_DATABASE_NAME=test_rev_${id}\`.
Verdict APPROVE only if no CRITICAL/HIGH/MEDIUM findings remain. LOW findings alone do not block. Be concrete: file, issue, fix.`,
    { label: `review${round}:${n.branch}`, phase: 'Review', schema: REVIEW_SCHEMA, model: 'sonnet' })
}

async function fix(id, n, findings, round) {
  return agent(`${COMMON(id, n)}

TASK: address review round ${round} findings on branch \`${n.branch}\`. You are in a fresh worktree. Run \`git worktree prune\`; if \`${n.branch}\` is checked out in another existing worktree (\`git worktree list\`), cd there and work in it; otherwise \`git checkout ${n.branch}\`. Fix every CRITICAL/HIGH/MEDIUM finding (and LOWs if cheap); if you believe a finding is wrong, explain why in summary instead of changing code. Re-run lint + tests green, commit to \`${n.branch}\`. Findings:\n${JSON.stringify(findings, null, 2)}\nReturn the structured result; head_sha = \`git rev-parse ${n.branch}\`.`,
    { label: `fix${round}:${n.branch}`, phase: 'Review', schema: IMPL_SCHEMA, isolation: 'worktree' })
}

let mergeChain = Promise.resolve()
function serialized(fn) { const p = mergeChain.then(fn, fn); mergeChain = p.catch(() => {}); return p }

async function merge(id, n, residual) {
  return serialized(() => agent(`You are the integration merger. Work ONLY in ${REPO} (the main checkout, on branch \`${INTEG}\`). \`${ENV}; export DJANGO_TEST_DATABASE_NAME=test_merge\`.
1. \`git checkout ${INTEG}\` (ensure clean tree), then \`git merge --no-ff ${n.branch} -m "Merge ${n.branch} into ${INTEG}"\`. Resolve conflicts preserving both sides' intent. Django migration conflicts: renumber/rebase the new branch's migrations or add a merge migration so \`makemigrations --check\` is clean and the graph is linear per app if possible.
2. Run the full gates (the full backend pytest takes ~20 min, longer than the 10-min foreground Bash limit: run it in the background writing to /tmp/merge-pytest.log and wait with the Monitor tool / run_in_background until it exits; never return before it finished): backend ruff check, ruff format --check, lint-imports, makemigrations --check --dry-run, full pytest; frontend lint, typecheck, npm test (if frontend changed since the last merge or in this branch). Fix any integration breakage with a follow-up commit (Conventional Commit, message ending with:\n${TRAILER}).
3. If and only if everything is green: \`git -c push.negotiate=true push origin ${INTEG}\` (shallow clone; a 'push negotiation failed' warning is harmless) (retry up to 4 times with backoff on network errors; if rejected because the remote has commits you lack, \`git fetch origin ${INTEG} && git merge --no-edit FETCH_HEAD\` then push). Never force-push.
If you cannot get it green, \`git merge --abort\` / reset ${INTEG} back to its pre-merge sha, and return ok=false with a precise explanation.
${residual.length ? 'Unresolved LOW/other review notes to append to the brief under this branch as known follow-ups: ' + JSON.stringify(residual) : ''}
Return merge_sha = \`git rev-parse ${INTEG}\` after pushing.`,
    { label: `merge:${n.branch}`, phase: 'Merge', schema: MERGE_SCHEMA }))
}

async function build(id) {
  const n = NODES[id]
  let r = n.pre ? { ok: true, owner_steps: [], summary: 'pre-implemented' } : await implement(id, n)
  if (!r) { log(`${n.branch}: implementer died`); return { ok: false } }
  log(`${n.branch}: implemented (${r.ok ? 'green' : 'NOT green'}) — owner steps: ${r.owner_steps.length}`)
  let residual = []
  let approved = false
  for (let round = 1; round <= 3; round++) {
    const v = await review(id, n, round)
    if (!v) break
    const blocking = v.findings.filter(f => f.severity !== 'LOW')
    residual = v.findings.filter(f => f.severity === 'LOW')
    if (v.verdict === 'APPROVE' && !blocking.length) { approved = true; log(`${n.branch}: APPROVE in round ${round}`); break }
    log(`${n.branch}: REVISE round ${round} (${blocking.length} blocking)`)
    const f = await fix(id, n, v.findings, round)
    if (f) r = f
    if (round === 3) { residual = residual.concat(blocking.map(b => ({ ...b, note: 'unresolved after 3 review rounds' }))) }
  }
  if (!approved) {
    const v = await review(id, n, 4)
    if (v && v.verdict === 'APPROVE') { approved = true; residual = v.findings }
    else if (v) { residual = v.findings; log(`${n.branch}: still REVISE after 3 fix rounds — merging with findings recorded as follow-ups`) }
  }
  const m = await merge(id, n, residual)
  if (!m || !m.ok) { log(`${n.branch}: MERGE FAILED — ${m ? m.notes : 'merger died'}`); return { ok: false, branch: n.branch, approved, owner_steps: r.owner_steps, notes: m ? m.notes : 'merger died' } }
  log(`${n.branch}: merged + pushed ${m.merge_sha.slice(0, 8)}`)
  return { ok: true, branch: n.branch, approved, owner_steps: r.owner_steps, residual, summary: r.summary }
}

const started = {}
function run(id) {
  if (!started[id]) {
    started[id] = Promise.all(NODES[id].deps.map(run)).then(depResults => {
      const bad = NODES[id].deps.filter((d, i) => !depResults[i] || !depResults[i].ok)
      if (bad.length) { log(`${NODES[id].branch}: skipped, failed deps ${bad.join(',')}`); return { ok: false, branch: NODES[id].branch, skipped: true } }
      return build(id)
    })
  }
  return started[id]
}

const PLAN_SCHEMA = {
  type: 'object',
  properties: { branches: { type: 'array', items: { type: 'object', properties: {
    id: { type: 'string', description: 'short id like p8a_1' }, branch: { type: 'string' }, section: { type: 'string', description: 'the exact heading of this branch\'s scope section in the brief' },
    deps: { type: 'array', items: { type: 'string' }, description: 'ids of other branches in THIS list it depends on' }, pre: { type: 'boolean' } }, required: ['id', 'branch', 'section', 'deps'] } } },
  required: ['branches'],
}

async function phaseRun(P, prevOk) {
  const briefPath = `docs/prompts/${P.dir}/brief.md`
  const docId = `${P.key}_brief`
  NODES[docId] = { branch: `docs/${P.key}-brief`, brief: briefPath, deps: [],
    section: `WRITE THE FULL BRIEF for ${P.title}, replacing the stub at ${briefPath} (keep its decisions/owner steps). Exact format and discipline of docs/prompts/phase-8a-learning-memory/brief.md and the Phase 5/6 briefs: goal, boundary, decisions with justification, scope one branch each with files/tests/done-when, a "Branch table" with columns # | Branch | Scope | Depends on | Status, out of scope, test strategy, risks, owner steps. Ground every branch in the REAL current code (read the apps it touches; earlier phases are already merged on the integration branch). Sources: the stub, ${PROPOSAL}, MASTER_PLAN §AA "Friday 3.0 phases", the ADRs it cites (ADR-012..015). ${MAC} Phase-specific: ${P.extra} Branches should be right-sized (roughly 4-8 server-side branches, each one coherent reviewable unit) and end with a closeout branch (brief status, MASTER_PLAN row status, preflight checks for the phase). Update the MASTER_PLAN row Brief/Status columns. Docs only, no code. Self-review against CLAUDE.md before finishing.` }
  const st = await agent(`Is \`git -C ${REPO} show ${INTEG}:${briefPath}\` still a stub (its header says "Stub")? Answer only.`, { label: `briefcheck:${P.key}`, phase: 'Implement', schema: { type: 'object', properties: { stub: { type: 'boolean' } }, required: ['stub'] }, effort: 'low' })
  const b = (st && !st.stub) ? { ok: true, branch: NODES[docId].branch, skipped_existing: true } : await run(docId)
  if (!b || !b.ok) { log(`${P.title}: brief not merged — phase skipped`); return [{ id: docId, ...(b || {}) }] }
  const plan = await agent(`Read the brief with \`git -C ${REPO} show ${INTEG}:${briefPath}\` and return its branch table as structured data in dependency order. EXCLUDE every row marked "deferred: Mac session" (or otherwise Mac/owner-only) and every branch already merged into ${INTEG} (\`git -C ${REPO} branch --merged ${INTEG}\`). Set pre=true for a branch that exists locally with commits ahead of ${INTEG} and whose brief row says implemented (it then goes straight to review). section = the exact heading of that branch's scope section. Do not edit anything.`, { label: `plan:${P.key}`, phase: 'Implement', schema: PLAN_SCHEMA })
  if (!plan) return [{ id: docId, ...b }]
  const ids = plan.branches.map(x => `${P.key}_${x.id}`)
  for (const x of plan.branches) {
    NODES[`${P.key}_${x.id}`] = { branch: x.branch, brief: briefPath, pre: !!x.pre, section: `${x.section} (${MAC})`,
      deps: x.deps.filter(d => plan.branches.some(o => o.id === d)).map(d => `${P.key}_${d}`) }
  }
  log(`${P.title}: ${ids.length} server-side branches planned`)
  const r = await Promise.all(ids.map(run))
  return [{ id: docId, ...b }].concat(ids.map((id, i) => ({ id, ...r[i] })))
}

const P8A = 'docs/prompts/phase-8a-learning-memory/brief.md'
async function finish8a() {
  const plan = await agent(`Read the brief with \`git -C ${REPO} show ${INTEG}:${P8A}\` and return its branch table as structured data in dependency order. EXCLUDE any branch already merged into ${INTEG} (check \`git -C ${REPO} branch --merged ${INTEG}\`; feat/p8-core-blocks is merged). Include feat/p8-ingestion-core (implemented, not yet merged). For deps on excluded (merged) branches, drop them. section = exact heading of that branch's scope section. Do not edit anything.`, { label: 'plan:p8a-rest', phase: 'Implement', schema: PLAN_SCHEMA })
  if (!plan) return []
  const ids = plan.branches.map(x => `p8a_${x.id}`)
  for (const x of plan.branches) {
    NODES[`p8a_${x.id}`] = { branch: x.branch, brief: P8A, section: x.section, pre: x.branch === 'feat/p8-ingestion-core',
      deps: x.deps.filter(d => plan.branches.some(o => o.id === d)).map(d => `p8a_${d}`) }
  }
  log(`Phase 8A rest: ${ids.length} branches`)
  const r = await Promise.all(ids.map(run))
  return ids.map((id, i) => ({ id, ...r[i] }))
}

NODES.tzfix = { branch: 'fix/tz-fact-learning-test', brief: '.swarm/README.md', deps: [],
  section: 'Known issue "Timezone bug in a test": memory/tests/test_fact_learning.py::test_valid_from_outside_one_day_is_clamped_and_counted and 2 sibling tests fail between 21:00 and 24:00 UTC because of the Europe/Istanbul date rollover. Find the root cause (test fixture vs production date math using UTC vs local date); fix the code if production is wrong, otherwise make the tests timezone-robust (freeze time at both 12:00 and 22:30 UTC as regression cases). Also grep the suite for the same pattern (timezone.now().date() vs localdate()) and fix siblings. Update the README known-issues line to resolved.' }
const all = []
for (const P of PHASES) { all.push(...(await phaseRun(P))) }
return all
