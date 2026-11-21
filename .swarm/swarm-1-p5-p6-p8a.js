export const meta = {
  name: 'friday-swarm-p5-p6-p8a',
  description: 'Friday 3.0: roadmap docs, finish Phase 5, Phase 6 voice+HUD, Phase 8A learning memory; Sonnet review loop per branch; serialized merge+test+push',
  phases: [
    { title: 'Implement', detail: 'one worktree agent per branch, dependency order' },
    { title: 'Review', detail: 'independent Sonnet reviewer until APPROVE (max 3 rounds)' },
    { title: 'Merge', detail: 'serialized merge into integration branch, full lint/test, push' },
  ],
}

const INTEG = 'claude/inspiring-franklin-pw7avo'
const REPO = '/home/user/jarvis-os'
const ENV = 'export DATABASE_URL=postgres://jarvis:jarvis@localhost:5432/jarvis REDIS_URL=redis://localhost:6379/0'
const TRAILER = 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01XEgeLKAHARtsKMgFxNJjAa'

const P5 = 'docs/prompts/phase-5-deploy-mcp/brief.md'
const P6 = 'docs/prompts/phase-6-voice-identity/brief.md'

const P8A = 'docs/prompts/phase-8a-learning-memory/brief.md'
const PROPOSAL = 'docs/research/2026-11-roadmap-proposal.md'
const NODES = {
  docs: { branch: 'docs/friday-3-roadmap', brief: PROPOSAL, section: `(e) "Exact document edits this plan requires" — the owner APPROVED every recommended answer of §g (d1-d10) on 2026-11-20. Apply all §e edits: restructure docs/MASTER_PLAN.md §AA into the Friday 3.0 phase table (execution order 5 → 6 → 8A → 9 → 8B → 10 → 11 → 12; Phase 7 Workshop kept as number 7 but moved to backlog; "later" list), record d1-d10 as decided in §O, note the OmniRoute verdict (avoid; copy routing-trace/circuit-breaker ideas into ai_core later) and the morluto/rea verdict (copy pattern; later a pinned tool inside harness discovery containers only, owner-authorization checkbox, spec output only) from the research; write the new ADRs as status "Accepted 2026-11-20" — IMPORTANT numbering: ADR-011 is already reserved by the approved Phase 6 brief for "voice transport", so renumber the proposal's ADRs consecutively from ADR-012 (privacy/ingestion pipeline, node protocol, node action classification, voice-turn safety) and fix every reference in the proposal and briefs; amend docs/prompts/phase-6-voice-identity/brief.md per the proposal (OpenRouter audio spike, Turkish eval set, latency trace, gateway audio caps, HUD v1 with health panel/accessibility, voice-turn safety, the MASTER_PLAN "Roadmap update 2026-11-20" voice additions) and keep its branch table consistent; WRITE docs/prompts/phase-8a-learning-memory/brief.md in the exact format and discipline of the Phase 5/6 briefs (goal, boundary, decisions with justification, scope one branch each with files/tests/done-when, a "Branch table" with columns # | Branch | Scope | Depends on, out of scope, test strategy, risks), grounded in the real memory/assistant/harness/ai_core code; and write SHORT stub briefs (goal + scope bullets + "full brief written at phase start") for phases 8B, 9, 10, 11, 12 under docs/prompts/. Docs only, no code. After writing, run your own critical self-review pass against CLAUDE.md rules.`, deps: [] },
  p5_7: { branch: 'feat/p5-home-assistant', brief: P5, section: '7. `feat/p5-home-assistant` — the branch ALREADY EXISTS on the remote (origin/feat/p5-home-assistant), reviewed, but was cut before branches 5/8 landed and "waits on the native-bridge reconciliation". After creating your branch from the integration branch, `git merge origin/feat/p5-home-assistant`, reconcile it with the native-bridge transport/data-class/precondition changes that landed with branches 5 and 8 (the code base was since renamed Jarvis→Friday and the external_installation placeholder became external_installation — adapt), finish anything incomplete, make everything green', deps: [] },
  p5_9: { branch: 'feat/p5-connectors-ui', brief: P5, section: '9. `feat/p5-connectors-ui`', deps: ['p5_7'] },
  p5_10: { branch: 'chore/p5-closeout', brief: P5, section: '10. closeout — PLUS (a) the `manage.py preflight` command described in docs/MASTER_PLAN.md "Roadmap update 2026-11-20" (live checks, WARN when a service is not configured; unit-tested with seams); (b) seed an OpenRouter provider row (`openai_compatible`, base URL https://openrouter.ai/api/v1, key via env OPENROUTER_API_KEY, chat capability, `personal_data_allowed=False` by default, disabled when the key is absent) with tests; (c) record that the old Phase 5 PR in the former repository is superseded by this integration branch', deps: ['p5_9', 'docs'] },
  p6_1: { branch: 'feat/p6-speech-gateway', brief: P6, section: '1. `feat/p6-speech-gateway` (as amended on 2026-11-20 — read the amendment, incl. the OpenRouter audio spike: verify from official OpenRouter docs via web search which STT/TTS endpoints exist and wire them through the gateway only if they exist; personal-data rule and audio caps apply)', deps: ['docs'] },
  p6_2: { branch: 'feat/p6-voice-api', brief: P6, section: '2. `feat/p6-voice-api` (as amended)', deps: ['p6_1'] },
  p6_3: { branch: 'feat/p6-push-to-talk', brief: P6, section: '3. `feat/p6-push-to-talk` (as amended — incl. finish window VOICE_FINISH_MS, interrupt words, mute flag inside the single speak function, deterministic voice routes before the brain incl. "hatırla ki" memory capture via the existing memory service and "bu ne kadar tuttu" ModelUsage receipt, receipts-only confirmations, voice-turn safety: a voice turn can read and propose, never execute)', deps: ['p6_2', 'p5_9'] },
  p6_4: { branch: 'feat/p6-wake-word', brief: P6, section: '4. `feat/p6-wake-word` (as amended; push-to-talk stays the default per owner decision d3; wake word "Hey Friday" needs a custom openWakeWord model — implement the manifest/hash/arming plumbing with a mocked detector and a configurable model; training the hey_friday model and Mac CPU/parity measurements are owner steps)', deps: ['p6_3'] },
  p6_5: { branch: 'feat/p6-visual-identity', brief: P6, section: '5. `feat/p6-visual-identity` (as amended: the animated JARVIS/Iron-Man-style HUD v1 — WebGL orb + HUD panels, health panel, accessibility, reduced motion; the "done on the Mac" precondition is an owner step, proceed and record it)', deps: ['p6_4'] },
  p6_6: { branch: 'chore/p6-closeout', brief: P6, section: '6. closeout (as amended; plus the Phase 6 checks in `manage.py preflight`)', deps: ['p6_5', 'p5_10'] },
}

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
Environment: Postgres(+pgvector) and Redis already run locally. Always \`${ENV}\` and set a UNIQUE test DB: \`export DJANGO_TEST_DATABASE_NAME=test_${id}\` (other agents run pytest concurrently). Backend: \`cd backend && uv sync --frozen\` then \`uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run python manage.py makemigrations --check --dry-run && uv run pytest -q\`. Frontend (if touched): \`cd frontend && npm ci && npm run lint && npm run typecheck && npm test\`. The machine has 4 CPUs; while iterating run targeted tests, but run the FULL backend suite once before you finish.
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
2. Run the full gates: backend ruff check, ruff format --check, lint-imports, makemigrations --check --dry-run, full pytest; frontend lint, typecheck, npm test (if frontend changed since the last merge or in this branch). Fix any integration breakage with a follow-up commit (Conventional Commit, message ending with:\n${TRAILER}).
3. If and only if everything is green: \`git push origin ${INTEG}\` (retry up to 4 times with backoff on network errors; if rejected because the remote has commits you lack, \`git fetch origin ${INTEG} && git merge --no-edit FETCH_HEAD\` then push). Never force-push.
If you cannot get it green, \`git merge --abort\` / reset ${INTEG} back to its pre-merge sha, and return ok=false with a precise explanation.
${residual.length ? 'Unresolved LOW/other review notes to append to the brief under this branch as known follow-ups: ' + JSON.stringify(residual) : ''}
Return merge_sha = \`git rev-parse ${INTEG}\` after pushing.`,
    { label: `merge:${n.branch}`, phase: 'Merge', schema: MERGE_SCHEMA }))
}

async function build(id) {
  const n = NODES[id]
  let r = await implement(id, n)
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
    deps: { type: 'array', items: { type: 'string' }, description: 'ids of other branches in THIS list it depends on' } }, required: ['id', 'branch', 'section', 'deps'] } } },
  required: ['branches'],
}
async function phase8a() {
  const d = await run('docs')
  if (!d || !d.ok) { log('Phase 8A skipped: roadmap docs not merged'); return [] }
  const p6 = await run('p6_6')
  const plan = await agent(`Read ${REPO}/${P8A} (on the local branch ${INTEG}: use \`git -C ${REPO} show ${INTEG}:${P8A}\`) and return its branch table as structured data, in dependency order. Exclude any closeout-only docs step only if the brief has none; include it if present. Do not edit anything.`, { label: 'plan:phase-8a', phase: 'Implement', schema: PLAN_SCHEMA })
  if (!plan) return []
  for (const b of plan.branches) {
    NODES[b.id] = { branch: b.branch, brief: P8A, section: b.section, deps: b.deps.filter(x => plan.branches.some(o => o.id === x)) }
    if (!b.deps.length) NODES[b.id].deps = []
  }
  log(`Phase 8A: ${plan.branches.length} branches planned (${p6 && p6.ok ? 'after Phase 6' : 'Phase 6 closeout not merged — continuing'})`)
  const r = await Promise.all(plan.branches.map(b => run(b.id)))
  return plan.branches.map((b, i) => ({ id: b.id, ...r[i] }))
}

const ids = Object.keys(NODES)
const [results, p8] = await Promise.all([Promise.all(ids.map(run)), phase8a()])
return ids.map((id, i) => ({ id, ...results[i] })).concat(p8)
