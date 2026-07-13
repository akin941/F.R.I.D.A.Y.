import { test, expect } from "@playwright/test";

// FakeAdapter's fixed reply (backend/ai_core/adapters/fake.py) — deterministic,
// no network call, so this string is a safe, stable assertion target.
const FAKE_REPLY = "Merhaba, ben Jarvis. MVP 1 gateway üzerinden çalışıyorum.";

// FakeAdapter's purpose-aware fixture for `memory.extraction` (MVP 2 §3):
// every assistant reply dispatches an extraction job over `on_commit`, run
// inline here because CI/e2e set `CELERY_TASK_ALWAYS_EAGER=true` (no broker
// in this job) — so by the time the reply's SSE `done` event lands, this
// suggestion already exists as a `pending_approval` Memory row.
const FAKE_MEMORY_TITLE = "Fake extracted preference";

test("signup, chat with the fake provider, curated memory, Today, logout, route guard", async ({
  page,
}) => {
  // Above the config default (45s): this path waits out two SSE replies, an
  // extraction-job round trip (dispatch -> worker pickup -> gateway call ->
  // Memory write), and a knowledge-ingestion round trip (dispatch -> worker
  // pickup -> embed -> KnowledgeChunk write) before their respective UI
  // states land, each with its own reload-poll budget (round-1 Codex
  // finding: the knowledge path's 20s+15s budget could exceed the original
  // 60s total once stacked after the memory path's own waits). The tasks
  // section added after knowledge is pure synchronous CRUD (no Celery job,
  // no reload-poll loop), so its own budget is small, but the total keeps
  // growing as sections stack. The approvals + vault export section added
  // last has its own 20s reload-poll budget (even though eager mode should
  // resolve near-instantly) — bumped from 135s to 150s for this section.
  // MVP 6's router section adds 3 more SSE round trips (knowledge_query
  // trigger, task_draft, tool_request) plus its own 20s approve+reload-poll
  // budget for the router-triggered vault export -- bumped to 220s. MVP 7's
  // Agents section adds two full agent lifecycles (goal -> plan -> read ->
  // task_draft confirm -> tool_request approval), each with its own
  // in-flight poll wait, plus the kill-switch path's own cancel + orphaned-
  // approval check -- bumped to 260s.
  test.setTimeout(260_000);

  const email = `e2e-${Date.now()}@jarvis.test`;
  const password = "Sup3rSecret!2026";

  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign up" }).click();

  await expect(page).toHaveURL(/\/chat$/);

  // Personal workspace auto-created on signup (workspaces/services.py).
  await expect(page.getByRole("combobox", { name: "Active workspace" })).toContainText(
    "Personal",
  );

  const newChatButton = page.getByRole("button", { name: "New chat" });
  await expect(newChatButton).toBeEnabled();
  await newChatButton.click();

  const composer = page.getByPlaceholder("Message Jarvis…");
  await expect(composer).toBeEnabled();
  await composer.fill("Hello, Jarvis");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText(FAKE_REPLY)).toBeVisible({ timeout: 15_000 });
  // The reply text can render from a streamed chunk slightly before the SSE
  // `done` event is processed; navigating away before then aborts the
  // stream, persists the message as `partial`, and skips extraction entirely
  // (assistant/services.py only dispatches on a `complete` persist). Wait for
  // the composer to leave streaming state ("Stop" -> "Send") first.
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();

  // Curated memory happy path (MVP 2 §6): the extraction fixture above
  // seeded a pending suggestion — curate it, then confirm the next chat
  // turn's `memories_used` picks it up as context.
  await page.getByRole("link", { name: "Memory" }).click();
  await expect(page).toHaveURL(/\/memory$/);

  // CardTitle (frontend/src/components/ui/card.tsx) is a plain <div>, not a
  // heading element, so this locates it by its text instead of an ARIA role.
  const memoryTitle = page.getByText(FAKE_MEMORY_TITLE, { exact: true });
  const memoryCard = memoryTitle.locator("../..");
  // The list loads once on mount; outside eager Celery mode (e.g. a local
  // `make up` stack with a real worker) the extraction job may still be
  // in flight when this page first loads, so reload until it lands.
  await expect(async () => {
    await page.reload();
    await expect(memoryTitle).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 20_000 });
  await memoryCard.getByRole("button", { name: "Approve" }).click();

  await expect(page.getByText("Nothing waiting for review.")).toBeVisible();
  await expect(memoryCard.getByText("Approved", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Chat" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await page.getByRole("button", { name: "Untitled conversation" }).click();
  await expect(composer).toBeEnabled();
  await composer.fill("What do you remember about me?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText(/Memory context: 1 memory/)).toBeVisible({ timeout: 15_000 });

  // Knowledge retrieval happy path (MVP 3 §4/§6): upload a short pasted-text
  // document, wait for `ready` (embedding dispatched over `on_commit`, run
  // inline here for the same CELERY_TASK_ALWAYS_EAGER reason as the memory
  // extraction job above), then confirm a chat reply actually retrieves it.
  // These exact strings are shared with ai_core/tests/test_fake_adapter.py
  // (`SEED_DOCUMENT_TEXT`/`QUERY_TEXT`), which proves the fake bag-of-words
  // embedding genuinely ranks this seed above unrelated noise before this
  // e2e test ever depends on that signal.
  const KNOWLEDGE_SEED_TEXT =
    "Jarvis OS supports offline lantern mode for camping trips without cell signal.";
  const KNOWLEDGE_QUERY_TEXT = "What lantern mode does Jarvis OS support when camping offline?";
  const KNOWLEDGE_DOC_TITLE = "Lantern mode notes";

  await page.getByRole("link", { name: "Knowledge" }).click();
  await expect(page).toHaveURL(/\/knowledge$/);

  await page.getByRole("button", { name: "New document" }).click();
  await page.getByLabel("Title").fill(KNOWLEDGE_DOC_TITLE);
  await page.getByLabel("Content").fill(KNOWLEDGE_SEED_TEXT);
  await page.getByRole("button", { name: "Create document" }).click();

  // Same reasoning as the memory-title lookup above: CardTitle is a plain
  // <div>, located by text; "../.." reaches the enclosing Card so the status
  // text (rendered in CardContent, a sibling of CardHeader) is in scope.
  const knowledgeTitle = page.getByText(KNOWLEDGE_DOC_TITLE, { exact: true });
  // Round-1 Codex finding: wait for the create POST to actually resolve and
  // render (via KnowledgeView's onCreated) BEFORE the first reload below —
  // reloading while that request is still in flight cancels it, so no
  // document would ever exist to reach `ready`, unlike the memory section
  // above where the reload loop is waiting on an independent background
  // job, not on a request this same page just issued.
  await expect(knowledgeTitle).toBeVisible({ timeout: 10_000 });
  const knowledgeCard = knowledgeTitle.locator("../..");
  await expect(async () => {
    await page.reload();
    await expect(knowledgeCard.getByText("Ready", { exact: true })).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 20_000 });

  await page.getByRole("link", { name: "Chat" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await page.getByRole("button", { name: "Untitled conversation" }).click();
  await expect(composer).toBeEnabled();
  await composer.fill(KNOWLEDGE_QUERY_TEXT);
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText(/Knowledge context: 1 chunk/)).toBeVisible({ timeout: 15_000 });

  // MVP 6 router happy path: all 3 live dispatch behaviors, in the same
  // conversation the knowledge doc above is already seeded in. FakeAdapter's
  // `router.intent` classification is content-triggered (backend/ai_core/
  // adapters/fake.py) purely so this real backend process — which this test
  // cannot monkeypatch — can still exercise every intent; the exact trigger
  // phrases mirror the ones the backend unit tests already send when they
  // monkeypatch `parse_intent` directly instead.
  //
  // knowledge_query: reuses the seed doc's own vocabulary (so the fake
  // bag-of-words embedding still ranks it above noise) with the trigger
  // phrase prepended -- proves a knowledge_query-classified turn still
  // grounds its reply in retrieval, not just that retrieval runs at all
  // (already covered above for the default `chat` intent). The widened-
  // budget mechanism itself is unit-tested (assistant/tests/test_context.py)
  // with real chunk counts exceeding the default cap; not worth re-proving
  // here with a dozen extra seeded documents.
  const knowledgeBadge = page.getByText(/Knowledge context: 1 chunk/);
  // The plain knowledge_query turn above already left one such badge on
  // this message list (MessageList renders one per assistant reply that
  // has chunks_used) -- a `.last()`/`.toBeVisible()` check alone could
  // still pass against THAT pre-existing badge without this router-
  // triggered turn ever producing its own new grounded reply (Codex round-2
  // review, MEDIUM: round-1's `.last()` fix wasn't sufficient, since
  // `.last()` on an already-matching locator resolves immediately). Waiting
  // for the COUNT to become 2 is the only way to actually prove a second,
  // new badge was added by this turn.
  await expect(knowledgeBadge).toHaveCount(1);
  await composer.fill(`Please summarize my knowledge base — ${KNOWLEDGE_QUERY_TEXT}`);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(knowledgeBadge).toHaveCount(2, { timeout: 15_000 });

  // task_draft: confirm creates a REAL Task via the router's draft card, not
  // the manual /tasks create form (that path is covered by the Tasks/Today
  // section below).
  const ROUTER_TASK_TITLE = "Fake extracted task";
  await expect(composer).toBeEnabled();
  await composer.fill("remind me to buy oat milk");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Looks like a task")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(ROUTER_TASK_TITLE, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Confirm task" }).click();
  await expect(page.getByText("Looks like a task")).not.toBeVisible();

  // tool_request: confirm creates a REAL ApprovalRequest via the router's
  // draft card. Approved the same way the manual Memory-page export button's
  // request is approved further below -- an in-page `fetch` against the BFF
  // transition endpoint, since the UI always hides Approve/Reject for the
  // requester's own request (brief §4, no bypass by design), and this
  // account has no second workspace member to be a genuine second decider.
  // Resolved to `succeeded` here, in this section, before the manual-button
  // section below creates its OWN separate vault_export request -- so
  // neither section's "find the pending vault_export request" lookup can
  // ever see more than the one request it's responsible for.
  await expect(composer).toBeEnabled();
  await composer.fill("export my vault please");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Looks like a tool request")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Confirm request" }).click();
  await expect(page.getByText("Looks like a tool request")).not.toBeVisible();
  await expect(page.getByText("Pending", { exact: true })).toBeVisible();

  const routerVaultExportRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the freshly signed-up user.");
    const requests = (await (
      await fetch(`/api/approvals/requests?workspace=${workspace.id}&status=pending`)
    ).json()) as { id: string; tool_name: string }[];
    const match = requests.find((r) => r.tool_name === "vault_export");
    if (!match) throw new Error("No pending vault_export approval request found (router path).");
    return match.id;
  });
  const routerApproveStatus = await page.evaluate(async (requestId) => {
    const response = await fetch(`/api/approvals/requests/${requestId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    return response.status;
  }, routerVaultExportRequestId);
  expect(routerApproveStatus).toBe(200);

  // Verify run status on /approvals, not by reloading /chat: the confirmed
  // request's ApprovalCard inside the chat composer is a one-time snapshot
  // (ToolRequestCard has no polling of its own, unlike ApprovalView's
  // 3-second in-flight poll) -- and reloading /chat itself would lose the
  // client-side conversation selection anyway, since it isn't part of the
  // URL. /approvals is the real, intended surface for checking run status.
  await page.getByRole("link", { name: "Approvals" }).click();
  await expect(page).toHaveURL(/\/approvals$/);
  await expect(async () => {
    await page.reload();
    await expect(page.getByText(/Run status: Succeeded/)).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 20_000 });

  // Tasks + Today happy path (MVP 4 §3): create a task due today on /tasks,
  // confirm it surfaces on /today (the client-local due_before filter from
  // brief §2 -- the server never computes "today" itself), complete it
  // there, confirm it drops off Today's open list. Pure synchronous CRUD,
  // no Celery job and no reload-poll loop needed (unlike the memory/
  // knowledge sections above).
  const TASK_TITLE = "Buy oat milk";

  function todayLocalDate(): string {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  await page.getByRole("link", { name: "Tasks" }).click();
  await expect(page).toHaveURL(/\/tasks$/);
  // Codex finding: if the initial `GET /api/tasks` (issued on mount) is
  // still in flight when the create form is submitted, it can resolve
  // *after* the create POST and overwrite state with the stale empty list
  // (TaskView.loadTasks unconditionally calls setTasks(list) on resolve).
  // This account is no longer task-free (the router's task_draft confirm
  // above already created one) -- waiting for THAT task to render is the
  // same "mount-time GET has settled" signal the original empty-list wait
  // proved, just adapted to this test's now-nonzero starting state.
  await expect(page.getByText(ROUTER_TASK_TITLE, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "New task" }).click();
  await page.getByLabel("Title").fill(TASK_TITLE);
  await page.getByLabel("Due date").fill(todayLocalDate());
  await page.getByRole("button", { name: "Create task" }).click();

  // Same reasoning as the memory/knowledge title lookups above: CardTitle is
  // a plain <div>, located by text, not an ARIA heading role.
  await expect(page.getByText(TASK_TITLE, { exact: true })).toBeVisible({ timeout: 10_000 });

  await page.getByRole("link", { name: "Today" }).click();
  await expect(page).toHaveURL(/\/today$/);
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();

  const todayTaskTitle = page.getByText(TASK_TITLE, { exact: true });
  await expect(todayTaskTitle).toBeVisible({ timeout: 10_000 });
  const todayTaskCard = todayTaskTitle.locator("../..");
  await todayTaskCard.getByRole("button", { name: "Complete" }).click();

  await expect(page.getByText("Nothing due today. Enjoy the quiet.")).toBeVisible();

  // Approvals + vault export happy path (MVP 5 §5): request the export from
  // Memory, approve it, confirm the run reaches succeeded. DEV_ALLOW_SELF_
  // APPROVAL is set for this e2e run only (no second workspace member exists
  // to be the decider, unlike a real deployment where requester != decider
  // is enforced with no exception) -- but the UI itself always hides
  // Approve/Reject for the requester's own request, everywhere, regardless
  // of that backend flag (brief §4's own-request gating has no bypass by
  // design). So the approve step below calls the BFF transition endpoint
  // directly via an in-page `fetch` (same authenticated browser context,
  // same CSRF-safe same-origin call a second admin's own browser would
  // make) rather than clicking a UI button that deliberately doesn't exist
  // for this actor. `OBSIDIAN_VAULT_PATH` points at a directory this run's
  // global-setup.ts already created; `CELERY_TASK_ALWAYS_EAGER=true` runs
  // the export inline, so the run should already be `succeeded` by the time
  // the approve call resolves -- the reload-poll loop below is just the
  // same defensive convention the memory/knowledge sections above use for
  // anything backed by a Celery-dispatched job.
  await page.getByRole("link", { name: "Memory" }).click();
  await expect(page).toHaveURL(/\/memory$/);
  await page.getByRole("button", { name: "Export to Obsidian vault" }).click();
  await expect(
    page.getByText(
      "Request created. It needs approval from another workspace admin before it runs.",
    ),
  ).toBeVisible();

  await page.getByRole("link", { name: "Approvals" }).click();
  await expect(page).toHaveURL(/\/approvals$/);
  // `.first()`: the router section above already left one DECIDED
  // vault_export entry on this page (Approved, listed after Pending in the
  // DOM per ApprovalView's own section order) -- this new request is the
  // Pending one, rendered first.
  await expect(page.getByText("vault_export").first()).toBeVisible();
  await expect(
    page.getByText("Waiting for another workspace admin to decide this request."),
  ).toBeVisible();

  const vaultExportRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the freshly signed-up user.");
    const requests = (await (
      await fetch(`/api/approvals/requests?workspace=${workspace.id}&status=pending`)
    ).json()) as { id: string; tool_name: string }[];
    const match = requests.find((r) => r.tool_name === "vault_export");
    if (!match) throw new Error("No pending vault_export approval request found.");
    return match.id;
  });
  const approveStatus = await page.evaluate(async (requestId) => {
    const response = await fetch(`/api/approvals/requests/${requestId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    return response.status;
  }, vaultExportRequestId);
  expect(approveStatus).toBe(200);

  await expect(async () => {
    await page.reload();
    // `.first()`: the router section above already left its own succeeded
    // vault_export run on this page, ordered by -created_at -- this newer
    // request's own run is listed first.
    await expect(page.getByText(/Run status: Succeeded/).first()).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 20_000 });

  // Agents happy path (MVP 7 §5/§6): a goal plans to a `read` step, a
  // `task_draft` step, and a `tool_request` step in one plan. FakeAdapter's
  // `agent.plan` purpose is content-triggered on the phrase "full plan"
  // (backend/ai_core/adapters/fake.py) purely so this real backend process --
  // which this test cannot monkeypatch -- can still exercise all three step
  // kinds in one run, mirroring the router section's identical convention
  // above. Unlike /memory, /knowledge, and /approvals above (real pages),
  // the agent detail view is pure client-side state on /agents (no
  // dedicated URL) -- AgentDetail.tsx polls itself every 3s while the Agent
  // is in flight, so waits below rely on that self-poll and a generous
  // `expect` timeout instead of the reload-poll convention used elsewhere
  // (reloading here would lose the selection and land back on the list).
  const AGENT_GOAL = "Give me the full plan to get my notes organized";
  const AGENT_READ_STEP = "Summarize relevant notes";
  const AGENT_TASK_DRAFT_STEP = "Draft a follow-up task";
  const AGENT_TOOL_REQUEST_STEP = "Export the vault";

  await page.getByRole("link", { name: "Agents" }).click();
  await expect(page).toHaveURL(/\/agents$/);

  await page.getByRole("button", { name: "New agent" }).click();
  await page.getByLabel("Goal").fill(AGENT_GOAL);
  await page.getByRole("button", { name: "Start agent" }).click();

  await expect(page.getByText(AGENT_GOAL, { exact: true })).toBeVisible({ timeout: 10_000 });
  await page.getByText(AGENT_GOAL, { exact: true }).click();
  await expect(page.getByRole("button", { name: /Back to agents/ })).toBeVisible({
    timeout: 10_000,
  });

  // Read step runs unattended; task_draft blocks for confirmation next.
  await expect(page.getByRole("button", { name: "Confirm task" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(AGENT_READ_STEP, { exact: true })).toBeVisible();
  await expect(page.getByText(AGENT_TASK_DRAFT_STEP, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Confirm task" }).click();

  // Confirming creates a real Task and advances to the tool_request step,
  // which requires approval (ADR-004) -- the engine can get no further
  // without a human decision, same "confirm here, decide there" split the
  // router's tool_request path already established.
  await expect(page.getByRole("link", { name: "Review on Approvals" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(AGENT_TOOL_REQUEST_STEP, { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Review on Approvals" }).click();
  await expect(page).toHaveURL(/\/approvals$/);

  const agentToolRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the freshly signed-up user.");
    const requests = (await (
      await fetch(`/api/approvals/requests?workspace=${workspace.id}&status=pending`)
    ).json()) as { id: string; tool_name: string }[];
    const match = requests.find((r) => r.tool_name === "vault_export");
    if (!match) throw new Error("No pending vault_export approval request found (agent path).");
    return match.id;
  });
  const agentApproveStatus = await page.evaluate(async (requestId) => {
    const response = await fetch(`/api/approvals/requests/${requestId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    return response.status;
  }, agentToolRequestId);
  expect(agentApproveStatus).toBe(200);

  await expect(async () => {
    await page.reload();
    await expect(page.getByText(/Run status: Succeeded/).first()).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 20_000 });

  // Approving resumes the engine (agents/signals.py), which runs the tool
  // step and, since it's the plan's last step, completes the whole agent.
  await page.getByRole("link", { name: "Agents" }).click();
  await expect(page).toHaveURL(/\/agents$/);
  await page.getByText(AGENT_GOAL, { exact: true }).click();
  await expect(page.getByText("Completed", { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Cancel agent" })).not.toBeVisible();

  // Agents kill-switch path (MVP 7 §3/§6): cancelling a `blocked_on_approval`
  // agent must be final -- even if the now-orphaned ApprovalRequest is
  // decided afterward, the Agent must stay `cancelled` and no further step
  // may run (the round-5 kill-switch-bypass fix from branch 3's review).
  const KILL_SWITCH_GOAL = "Give me the full plan to test the kill switch";

  // Back to the list via the in-page button, not the "Agents" nav link --
  // we're already on /agents (viewing agent1's detail, which is pure
  // client-side state with no URL of its own), so a Link to the same path
  // is a no-op in Next.js and would leave the detail view showing forever.
  await page.getByRole("button", { name: /Back to agents/ }).click();
  await page.getByRole("button", { name: "New agent" }).click();
  await page.getByLabel("Goal").fill(KILL_SWITCH_GOAL);
  await page.getByRole("button", { name: "Start agent" }).click();

  await expect(page.getByText(KILL_SWITCH_GOAL, { exact: true })).toBeVisible({ timeout: 10_000 });
  await page.getByText(KILL_SWITCH_GOAL, { exact: true }).click();
  await expect(page.getByRole("button", { name: /Back to agents/ })).toBeVisible({
    timeout: 10_000,
  });

  await expect(page.getByRole("button", { name: "Confirm task" })).toBeVisible({
    timeout: 20_000,
  });
  await page.getByRole("button", { name: "Confirm task" }).click();

  await expect(page.getByRole("button", { name: "Cancel agent" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("link", { name: "Review on Approvals" })).toBeVisible({
    timeout: 20_000,
  });

  await page.getByRole("button", { name: "Cancel agent" }).click();
  await expect(page.getByRole("button", { name: "Cancel agent" })).not.toBeVisible();
  await expect(page.getByText("Cancelled", { exact: true })).toBeVisible();

  const killSwitchRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the freshly signed-up user.");
    const requests = (await (
      await fetch(`/api/approvals/requests?workspace=${workspace.id}&status=pending`)
    ).json()) as { id: string; tool_name: string }[];
    const match = requests.find((r) => r.tool_name === "vault_export");
    if (!match) throw new Error("No pending vault_export approval request found (kill-switch).");
    return match.id;
  });
  const killSwitchApproveStatus = await page.evaluate(async (requestId) => {
    const response = await fetch(`/api/approvals/requests/${requestId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    return response.status;
  }, killSwitchRequestId);
  // Deciding the now-orphaned request is still a valid approvals-side action
  // (approvals has no idea its requesting agent was cancelled) -- the
  // guarantee under test is entirely on the Agent's own side, below. A page
  // reload would lose the client-side detail selection (same reasoning as
  // above), so re-open the agent via the list instead of reloading in place
  // -- this also forces a real re-fetch from the backend, not a stale local
  // state that never learned about the approval decision. Back-to-list is
  // via the in-page button, not the "Agents" nav link: we're already on
  // /agents (still viewing agent2's own detail), so a Link to the same path
  // is a no-op in Next.js.
  expect(killSwitchApproveStatus).toBe(200);

  await page.getByRole("button", { name: /Back to agents/ }).click();
  await page.getByText(KILL_SWITCH_GOAL, { exact: true }).click();
  await expect(page.getByText("Cancelled", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Cancel agent" })).not.toBeVisible();

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  // Optimistic route guard: an unauthenticated request for a protected path
  // redirects back to /login (src/proxy.ts) rather than rendering the shell.
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
});
