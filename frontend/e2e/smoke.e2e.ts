import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { issuePairingCode } from "./manage";

// FakeAdapter's fixed reply (backend/ai_core/adapters/fake.py) — deterministic,
// no network call, so this string is a safe, stable assertion target.
const FAKE_REPLY = "Merhaba, ben Jarvis. MVP 1 gateway üzerinden çalışıyorum.";

// MVP 9 §5/§6: the PAT/Telegram sections below talk to the real backend
// directly (Playwright's `request` fixture, not `page.evaluate` fetch) --
// a PAT is meant to be usable by a standalone HTTP client with no browser
// session at all, and a webhook call is never made by our own frontend in
// the first place. `page.evaluate` fetches stay same-origin (the BFF, port
// 3001) throughout this file; only these two new sections cross to the raw
// backend origin, which is also why they use `request.*` instead --
// `page.evaluate`'s fetch runs inside the real browser page and would be
// subject to CORS against a different origin, while `request.*` is a plain
// Node HTTP client with no such restriction.
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8001/api/v1";
// Matches the fixture value this repo's compose.yml/ci.yml set for the e2e
// environment -- a deliberately fake, non-secret value (MVP9 §4's webhook
// auth exists to prove authenticity, not to hide anything in a dev/test
// stack that already runs an insecure Django secret key and a fake LLM
// provider).
const TELEGRAM_WEBHOOK_SECRET = process.env.TELEGRAM_WEBHOOK_SECRET ?? "e2e-fixture-webhook-secret";

// Clicking an agent's goal card opens its routed detail page
// (`/agents/[agentId]?workspace=`, Phase 4 branch 3). Retrying the click
// until the detail page's own "Ajanlara dön" link (rendered immediately,
// regardless of the agent's own data still loading) shows up absorbs a
// click that lands before hydration without weakening what the test
// proves.
async function openAgentDetail(page: Page, goalText: string) {
  await expect(async () => {
    await page.getByText(goalText, { exact: true }).click();
    await expect(page.getByRole("link", { name: /Ajanlara dön/ })).toBeVisible({
      timeout: 3_000,
    });
  }).toPass({ timeout: 15_000 });
}

// FakeAdapter's purpose-aware fixture for `memory.extraction` (MVP 2 §3):
// every assistant reply dispatches an extraction job over `on_commit`, run
// inline here because CI/e2e set `CELERY_TASK_ALWAYS_EAGER=true` (no broker
// in this job) — so by the time the reply's SSE `done` event lands, this
// suggestion already exists as a `pending_approval` Memory row.
const FAKE_MEMORY_TITLE = "Fake extracted preference";

test("enroll, chat with the fake provider, curated memory, Today, logout, route guard", async ({
  page,
  request,
}, testInfo) => {
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
  // approval check -- bumped to 260s. MVP 8's skill-matched agent lifecycle
  // (goal -> agent.skill_match -> read -> tool_request approval, no
  // task_draft) adds one more full round trip -- bumped to 300s. MVP 9's
  // PAT/Telegram section adds a raw-backend chat round trip (no reload-poll
  // needed, `request.post` already waits for the full SSE stream) plus two
  // Telegram webhook-driven reload-poll waits (link-status, then a relayed
  // message's persistence) -- bumped to 340s.
  test.setTimeout(340_000);

  // Device trust (Phase 4 branch 2): no signup -- this browser is enrolled
  // as a trusted device of the owner with a host-issued pairing PIN
  // (global-setup.ts). A retry needs a fresh one (codes are single-use).
  const pairingCode =
    testInfo.retry === 0 && process.env.E2E_PAIRING_CODE
      ? process.env.E2E_PAIRING_CODE
      : issuePairingCode();
  await page.goto("/enroll");
  await page.getByLabel("Eşleştirme kodu").fill(pairingCode);
  await page.getByLabel("Cihaz adı").fill("e2e browser");
  await page.getByRole("button", { name: "Cihazı eşleştir" }).click();

  await expect(page).toHaveURL(/\/chat$/);

  // The owner's personal workspace (bootstrap_owner -> register_user).
  await expect(page.getByRole("combobox", { name: "Aktif çalışma alanı" })).toContainText(
    "Personal",
  );

  const newChatButton = page.getByRole("button", { name: "Yeni sohbet" });
  await expect(newChatButton).toBeEnabled();
  await newChatButton.click();

  const composer = page.getByPlaceholder("Jarvis'e yaz…");
  await expect(composer).toBeEnabled();
  await composer.fill("Hello, Jarvis");
  await page.getByRole("button", { name: "Gönder" }).click();

  await expect(page.getByText(FAKE_REPLY)).toBeVisible({ timeout: 15_000 });
  // The reply text can render from a streamed chunk slightly before the SSE
  // `done` event is processed; navigating away before then aborts the
  // stream, persists the message as `partial`, and skips extraction entirely
  // (assistant/services.py only dispatches on a `complete` persist). Wait for
  // the composer to leave streaming state ("Durdur" -> "Gönder") first.
  await expect(page.getByRole("button", { name: "Gönder" })).toBeVisible();

  // Curated memory happy path (MVP 2 §6): the extraction fixture above
  // seeded a pending suggestion — curate it, then confirm the next chat
  // turn's `memories_used` picks it up as context.
  await page.getByRole("link", { name: "Hafıza" }).click();
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
  await memoryCard.getByRole("button", { name: "Onayla" }).click();

  await expect(page.getByText("İncelemeyi bekleyen bir şey yok.")).toBeVisible();
  await expect(memoryCard.getByText("Onaylandı", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Sohbet" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await page.getByRole("button", { name: "Adsız sohbet" }).click();
  await expect(composer).toBeEnabled();
  await composer.fill("What do you remember about me?");
  await page.getByRole("button", { name: "Gönder" }).click();

  await expect(page.getByText(/Hafıza bağlamı: 1 not/)).toBeVisible({ timeout: 15_000 });

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

  await page.getByRole("link", { name: "Bilgi" }).click();
  await expect(page).toHaveURL(/\/knowledge$/);

  await page.getByRole("button", { name: "Yeni belge" }).click();
  await page.getByLabel("Başlık").fill(KNOWLEDGE_DOC_TITLE);
  await page.getByLabel("İçerik").fill(KNOWLEDGE_SEED_TEXT);
  await page.getByRole("button", { name: "Belge oluştur" }).click();

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
    await expect(knowledgeCard.getByText("Hazır", { exact: true })).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 20_000 });

  await page.getByRole("link", { name: "Sohbet" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await page.getByRole("button", { name: "Adsız sohbet" }).click();
  await expect(composer).toBeEnabled();
  await composer.fill(KNOWLEDGE_QUERY_TEXT);
  await page.getByRole("button", { name: "Gönder" }).click();

  await expect(page.getByText(/Bilgi bağlamı: 1 parça/)).toBeVisible({ timeout: 15_000 });

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
  const knowledgeBadge = page.getByText(/Bilgi bağlamı: 1 parça/);
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
  await page.getByRole("button", { name: "Gönder" }).click();
  await expect(knowledgeBadge).toHaveCount(2, { timeout: 15_000 });

  // task_draft: confirm creates a REAL Task via the router's draft card, not
  // the manual /tasks create form (that path is covered by the Tasks/Today
  // section below).
  const ROUTER_TASK_TITLE = "Fake extracted task";
  await expect(composer).toBeEnabled();
  await composer.fill("remind me to buy oat milk");
  await page.getByRole("button", { name: "Gönder" }).click();
  await expect(page.getByText("Bu bir görev gibi görünüyor")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(ROUTER_TASK_TITLE, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Görevi onayla" }).click();
  await expect(page.getByText("Bu bir görev gibi görünüyor")).not.toBeVisible();

  // tool_request: confirm creates a REAL ApprovalRequest via the router's
  // draft card. Approved the same way the manual Memory-page export button's
  // request is approved further below -- an in-page `fetch` against the BFF
  // transition endpoint. (Under ADR-008's single-owner model the owner may
  // decide their own requests in the UI too, but that path now has a
  // confirmation step; the direct call keeps this section focused on the
  // router, not the approvals card.)
  // Resolved to `succeeded` here, in this section, before the manual-button
  // section below creates its OWN separate vault_export request -- so
  // neither section's "find the pending vault_export request" lookup can
  // ever see more than the one request it's responsible for.
  await expect(composer).toBeEnabled();
  await composer.fill("export my vault please");
  await page.getByRole("button", { name: "Gönder" }).click();
  await expect(page.getByText("Bu bir araç isteği gibi görünüyor")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "İsteği onayla" }).click();
  await expect(page.getByText("Bu bir araç isteği gibi görünüyor")).not.toBeVisible();
  await expect(page.getByText("Beklemede", { exact: true })).toBeVisible();

  const routerVaultExportRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the owner.");
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
  await page.getByRole("link", { name: "Onaylar", exact: true }).click();
  await expect(page).toHaveURL(/\/approvals$/);
  await expect(async () => {
    await page.reload();
    await expect(page.getByText(/Çalışma durumu: Başarılı/)).toBeVisible({ timeout: 5_000 });
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

  await page.getByRole("link", { name: "Görevler" }).click();
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

  await page.getByRole("button", { name: "Yeni görev" }).click();
  await page.getByLabel("Başlık").fill(TASK_TITLE);
  await page.getByLabel("Son tarih").fill(todayLocalDate());
  await page.getByRole("button", { name: "Görev oluştur" }).click();

  // Same reasoning as the memory/knowledge title lookups above: CardTitle is
  // a plain <div>, located by text, not an ARIA heading role.
  await expect(page.getByText(TASK_TITLE, { exact: true })).toBeVisible({ timeout: 10_000 });

  await page.getByRole("link", { name: "Bugün" }).click();
  await expect(page).toHaveURL(/\/today$/);
  await expect(page.getByRole("heading", { name: "Bugün" })).toBeVisible();

  const todayTaskTitle = page.getByText(TASK_TITLE, { exact: true });
  await expect(todayTaskTitle).toBeVisible({ timeout: 10_000 });
  const todayTaskCard = todayTaskTitle.locator("../..");
  await todayTaskCard.getByRole("button", { name: "Tamamla" }).click();

  await expect(page.getByText("Bugün vadesi gelen bir şey yok. Keyfini çıkar.")).toBeVisible();

  // Approvals + vault export happy path (MVP 5 §5): request the export from
  // Memory, approve it, confirm the run reaches succeeded. The approve step
  // below calls the BFF transition endpoint directly via an in-page `fetch`
  // (same authenticated browser context, same CSRF-safe same-origin call
  // the approvals card itself makes) -- the card's own Onayla button now
  // sits behind a confirmation step, which the unit tests cover.
  // `OBSIDIAN_VAULT_PATH` points at a directory this run's
  // global-setup.ts already created; `CELERY_TASK_ALWAYS_EAGER=true` runs
  // the export inline, so the run should already be `succeeded` by the time
  // the approve call resolves -- the reload-poll loop below is just the
  // same defensive convention the memory/knowledge sections above use for
  // anything backed by a Celery-dispatched job.
  await page.getByRole("link", { name: "Hafıza" }).click();
  await expect(page).toHaveURL(/\/memory$/);
  await page.getByRole("button", { name: "Obsidian vault'a aktar" }).click();
  await expect(
    page.getByText(
      "İstek oluşturuldu. Çalışmadan önce başka bir çalışma alanı yöneticisinin onayı gerekiyor.",
    ),
  ).toBeVisible();

  await page.getByRole("link", { name: "Onaylar", exact: true }).click();
  await expect(page).toHaveURL(/\/approvals$/);
  // `.first()`: the router section above already left one DECIDED
  // vault_export entry on this page ("Onaylandı", listed after "Beklemede"
  // in the DOM per ApprovalView's own section order) -- this new request is
  // the pending ("Beklemede") one, rendered first.
  await expect(page.getByText("vault_export").first()).toBeVisible();
  // The old "waiting for another workspace admin to decide" notice no longer
  // exists (ADR-008 single-owner: the owner decides their own requests).
  // Nearest current equivalent: the pending section's heading shows exactly
  // this one undecided request.
  await expect(page.getByRole("heading", { name: /Bekleyenler \(1\)/ })).toBeVisible();

  const vaultExportRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the owner.");
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
    await expect(page.getByText(/Çalışma durumu: Başarılı/).first()).toBeVisible({ timeout: 5_000 });
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

  await page.getByRole("link", { name: "Ajanlar" }).click();
  await expect(page).toHaveURL(/\/agents$/);

  await page.getByRole("button", { name: "Yeni ajan" }).click();
  await page.getByLabel("Hedef").fill(AGENT_GOAL);
  await page.getByRole("button", { name: "Ajanı başlat" }).click();

  await expect(page.getByText(AGENT_GOAL, { exact: true })).toBeVisible({ timeout: 10_000 });
  await openAgentDetail(page, AGENT_GOAL);

  // Read step runs unattended; task_draft blocks for confirmation next.
  await expect(page.getByRole("button", { name: "Görevi onayla" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(AGENT_READ_STEP, { exact: true })).toBeVisible();
  await expect(page.getByText(AGENT_TASK_DRAFT_STEP, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Görevi onayla" }).click();

  // Confirming creates a real Task and advances to the tool_request step,
  // which requires approval (ADR-004) -- the engine can get no further
  // without a human decision, same "confirm here, decide there" split the
  // router's tool_request path already established.
  await expect(page.getByRole("link", { name: "Onaylar sayfasında incele" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(AGENT_TOOL_REQUEST_STEP, { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Onaylar sayfasında incele" }).click();
  await expect(page).toHaveURL(/\/approvals$/);

  const agentToolRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the owner.");
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
    await expect(page.getByText(/Çalışma durumu: Başarılı/).first()).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 20_000 });

  // Approving resumes the engine (agents/signals.py), which runs the tool
  // step and, since it's the plan's last step, completes the whole agent.
  await page.getByRole("link", { name: "Ajanlar" }).click();
  await expect(page).toHaveURL(/\/agents$/);
  await openAgentDetail(page, AGENT_GOAL);
  await expect(page.getByText("Tamamlandı", { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Ajanı iptal et" })).not.toBeVisible();

  // Agents skill-matched path (MVP 8 §3/§7, brief): a goal matching a
  // registered skill's trigger phrase skips `agent.plan` entirely --
  // FakeAdapter's `agent.skill_match` purpose is content-triggered on
  // "export my weekly notes" (backend/ai_core/adapters/fake.py), which
  // returns the `weekly_export` skill's fixed two-step template (read ->
  // tool_request) rather than a freeform LLM plan. Asserting the resulting
  // steps match that exact template (not the "full plan" trigger's
  // three-kind fixture above, and no "Görevi onayla" button at all -- the
  // skill's template has no task_draft step) is what actually proves the
  // skip happened, not just that some plan ran.
  const SKILL_MATCHED_GOAL = "Please export my weekly notes for me";
  const SKILL_READ_STEP = "Summarize recent notes";
  const SKILL_TOOL_REQUEST_STEP = "Export the vault";

  await page.getByRole("link", { name: /Ajanlara dön/ }).click();
  await page.getByRole("button", { name: "Yeni ajan" }).click();
  await page.getByLabel("Hedef").fill(SKILL_MATCHED_GOAL);
  await page.getByRole("button", { name: "Ajanı başlat" }).click();

  await expect(page.getByText(SKILL_MATCHED_GOAL, { exact: true })).toBeVisible({
    timeout: 10_000,
  });
  await openAgentDetail(page, SKILL_MATCHED_GOAL);
  await expect(page.getByText("Şu beceriyle çalıştı: weekly_export", { exact: true })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText(SKILL_READ_STEP, { exact: true })).toBeVisible();

  // Read step runs unattended; tool_request blocks for approval next --
  // no "Görevi onayla" ever appears on this path (the skill's template has
  // no task_draft step at all).
  await expect(page.getByRole("link", { name: "Onaylar sayfasında incele" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(SKILL_TOOL_REQUEST_STEP, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Görevi onayla" })).not.toBeVisible();

  await page.getByRole("link", { name: "Onaylar sayfasında incele" }).click();
  await expect(page).toHaveURL(/\/approvals$/);

  const skillToolRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the owner.");
    const requests = (await (
      await fetch(`/api/approvals/requests?workspace=${workspace.id}&status=pending`)
    ).json()) as { id: string; tool_name: string }[];
    const match = requests.find((r) => r.tool_name === "vault_export");
    if (!match) throw new Error("No pending vault_export approval request found (skill path).");
    return match.id;
  });
  const skillApproveStatus = await page.evaluate(async (requestId) => {
    const response = await fetch(`/api/approvals/requests/${requestId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    return response.status;
  }, skillToolRequestId);
  expect(skillApproveStatus).toBe(200);

  await page.getByRole("link", { name: "Ajanlar" }).click();
  await expect(page).toHaveURL(/\/agents$/);
  await openAgentDetail(page, SKILL_MATCHED_GOAL);
  await expect(page.getByText("Tamamlandı", { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Ajanı iptal et" })).not.toBeVisible();

  // Agents kill-switch path (MVP 7 §3/§6): cancelling a `blocked_on_approval`
  // agent must be final -- even if the now-orphaned ApprovalRequest is
  // decided afterward, the Agent must stay `cancelled` and no further step
  // may run (the round-5 kill-switch-bypass fix from branch 3's review).
  const KILL_SWITCH_GOAL = "Give me the full plan to test the kill switch";

  // Back to the list via the in-page button, not the "Ajanlar" nav link --
  // we're already on /agents (viewing agent1's detail, which is pure
  // client-side state with no URL of its own), so a Link to the same path
  // is a no-op in Next.js and would leave the detail view showing forever.
  await page.getByRole("link", { name: /Ajanlara dön/ }).click();
  await page.getByRole("button", { name: "Yeni ajan" }).click();
  await page.getByLabel("Hedef").fill(KILL_SWITCH_GOAL);
  await page.getByRole("button", { name: "Ajanı başlat" }).click();

  await expect(page.getByText(KILL_SWITCH_GOAL, { exact: true })).toBeVisible({ timeout: 10_000 });
  await openAgentDetail(page, KILL_SWITCH_GOAL);

  await expect(page.getByRole("button", { name: "Görevi onayla" })).toBeVisible({
    timeout: 20_000,
  });
  await page.getByRole("button", { name: "Görevi onayla" }).click();

  await expect(page.getByRole("button", { name: "Ajanı iptal et" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("link", { name: "Onaylar sayfasında incele" })).toBeVisible({
    timeout: 20_000,
  });

  await page.getByRole("button", { name: "Ajanı iptal et" }).click();
  await expect(page.getByRole("button", { name: "Ajanı iptal et" })).not.toBeVisible();
  await expect(page.getByText("İptal edildi", { exact: true })).toBeVisible();

  const killSwitchRequestId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the owner.");
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
  // via the in-page button, not the "Ajanlar" nav link: we're already on
  // /agents (still viewing agent2's own detail), so a Link to the same path
  // is a no-op in Next.js.
  expect(killSwitchApproveStatus).toBe(200);

  await page.getByRole("link", { name: /Ajanlara dön/ }).click();
  await openAgentDetail(page, KILL_SWITCH_GOAL);
  await expect(page.getByText("İptal edildi", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Ajanı iptal et" })).not.toBeVisible();

  // PAT/CLI channel happy path (MVP 9 §1/§2): a PAT-authenticated request
  // hits the REAL backend directly, with no browser session at all -- this
  // is the actual point of the channel, so it's the one thing this section
  // must prove that no earlier one does. Minting the token is still a web/
  // JWT-only action (brief §1), so that one call goes through the BFF via
  // the existing browser session; everything after uses `request.*`
  // against BACKEND_API_URL with only the raw `jpat_...` bearer token.
  const e2eWorkspaceId = await page.evaluate(async () => {
    const workspaces = (await (await fetch("/api/workspaces")).json()) as { id: string }[];
    const workspace = workspaces[0];
    if (!workspace) throw new Error("No workspace found for the owner.");
    return workspace.id;
  });

  const mintResponse = await page.evaluate(async (workspaceId) => {
    const response = await fetch("/api/auth/tokens", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace: workspaceId, name: "e2e cli" }),
    });
    return { status: response.status, body: (await response.json()) as { token?: string } };
  }, e2eWorkspaceId);
  expect(mintResponse.status).toBe(201);
  const patToken = mintResponse.body.token;
  if (!patToken) throw new Error("Token mint response had no raw `token` field.");
  const patAuthHeader = { Authorization: `Bearer ${patToken}` };

  // No `workspace` in the body at all -- implied by the bound credential
  // (core.channels.scoped_for_actor), proving the round-3 P2 fix actually
  // works end-to-end, not just against a mocked request in a unit test.
  const createConvResponse = await request.post(`${BACKEND_API_URL}/ai/conversations`, {
    headers: patAuthHeader,
    data: {},
  });
  expect(createConvResponse.ok()).toBe(true);
  const patConversationId = ((await createConvResponse.json()) as { id: string }).id;

  const patMessageResponse = await request.post(
    `${BACKEND_API_URL}/ai/conversations/${patConversationId}/messages`,
    { headers: patAuthHeader, data: { content: "Hello from the CLI channel" } },
  );
  expect(patMessageResponse.ok()).toBe(true);

  const patMessages = (await (
    await request.get(`${BACKEND_API_URL}/ai/conversations/${patConversationId}/messages`, {
      headers: patAuthHeader,
    })
  ).json()) as { role: string; content: string }[];
  expect(patMessages.some((m) => m.role === "assistant" && m.content === FAKE_REPLY)).toBe(true);

  // Channel restriction on decisions (MVP 9 §1, ADR-004) -- this MVP's core
  // safety property per the brief's own Risks section, so it's the one
  // assertion in this whole file that's worth proving against the real
  // backend rather than only in a unit test: a PAT-authenticated actor may
  // still PROPOSE a tool_request (creation is unchanged, brief's own
  // "Out of scope" note) but must never be able to DECIDE one.
  const patApprovalCreateResponse = await request.post(`${BACKEND_API_URL}/approvals/requests`, {
    headers: patAuthHeader,
    data: { tool_name: "vault_export", payload: {} },
  });
  expect(patApprovalCreateResponse.ok()).toBe(true);
  const patApprovalRequestId = ((await patApprovalCreateResponse.json()) as { id: string }).id;

  const patApproveResponse = await request.post(
    `${BACKEND_API_URL}/approvals/requests/${patApprovalRequestId}/approve`,
    { headers: patAuthHeader },
  );
  expect(patApproveResponse.status()).toBe(403);

  // The same request, decided over the web channel instead -- proving the
  // 403 above is a channel restriction, not a general failure of this
  // request or account.
  const webApproveStatus = await page.evaluate(async (requestId) => {
    const response = await fetch(`/api/approvals/requests/${requestId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    return response.status;
  }, patApprovalRequestId);
  expect(webApproveStatus).toBe(200);

  // Telegram happy path (MVP 9 §4): a webhook POST is the one genuinely
  // external-facing surface this whole file exercises -- Telegram is not
  // our own frontend, so this goes through `request.*` against the raw
  // backend, with the shared-secret header a real Telegram delivery would
  // carry. `process_telegram_update`'s reply-sending step still makes one
  // real, bounded (10s timeout, branch-3-round-4 fix), fast-failing outbound
  // call to api.telegram.org (the fixture bot token isn't real) -- accepted
  // deliberately (see docs/integrations/README.md) since the state this
  // section actually asserts on (the link, the persisted messages) is fully
  // committed before that call ever happens; this webhook response's own
  // status is intentionally not asserted on for that reason.
  const telegramLinkCodeResponse = await page.evaluate(async (workspaceId) => {
    const response = await fetch("/api/integrations/telegram/link-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace: workspaceId }),
    });
    return (await response.json()) as { code: string };
  }, e2eWorkspaceId);

  const conversationsBeforeLink = (await (
    await request.get(`${BACKEND_API_URL}/ai/conversations?workspace=${e2eWorkspaceId}`, {
      headers: patAuthHeader,
    })
  ).json()) as { id: string }[];

  const telegramChatId = Date.now();
  await request
    .post(`${BACKEND_API_URL}/integrations/telegram/webhook`, {
      headers: { "X-Telegram-Bot-Api-Secret-Token": TELEGRAM_WEBHOOK_SECRET },
      data: {
        update_id: 1,
        message: {
          chat: { id: telegramChatId, type: "private" },
          text: `/link ${telegramLinkCodeResponse.code}`,
        },
      },
    })
    .catch(() => undefined);

  await expect(async () => {
    const status = (await (
      await page.evaluate(async (workspaceId) => {
        const response = await fetch(
          `/api/integrations/telegram/link-status?workspace=${workspaceId}`,
        );
        return { status: response.status, body: await response.json() };
      }, e2eWorkspaceId)
    )) as { body: { linked: boolean; chat_id: string | null } };
    expect(status.body.linked).toBe(true);
    expect(status.body.chat_id).toBe(String(telegramChatId));
  }).toPass({ timeout: 20_000 });

  const conversationsAfterLink = (await (
    await request.get(`${BACKEND_API_URL}/ai/conversations?workspace=${e2eWorkspaceId}`, {
      headers: patAuthHeader,
    })
  ).json()) as { id: string }[];
  const beforeIds = new Set(conversationsBeforeLink.map((c) => c.id));
  const linkedConversationId = conversationsAfterLink.find((c) => !beforeIds.has(c.id))?.id;
  if (!linkedConversationId) {
    throw new Error("Linking Telegram did not create a new Conversation.");
  }

  await request
    .post(`${BACKEND_API_URL}/integrations/telegram/webhook`, {
      headers: { "X-Telegram-Bot-Api-Secret-Token": TELEGRAM_WEBHOOK_SECRET },
      data: {
        update_id: 2,
        message: { chat: { id: telegramChatId, type: "private" }, text: "Hello from Telegram" },
      },
    })
    .catch(() => undefined);

  await expect(async () => {
    const messages = (await (
      await request.get(
        `${BACKEND_API_URL}/ai/conversations/${linkedConversationId}/messages`,
        { headers: patAuthHeader },
      )
    ).json()) as { role: string; content: string }[];
    expect(messages.some((m) => m.role === "user" && m.content === "Hello from Telegram")).toBe(
      true,
    );
    expect(messages.some((m) => m.role === "assistant" && m.content === FAKE_REPLY)).toBe(true);
  }).toPass({ timeout: 20_000 });

  // "Çıkış yap" ends the session; the device stays trusted.
  await page.getByRole("button", { name: "Çıkış yap" }).click();
  await expect(page).toHaveURL(/\/enroll\?signed_out=1$/);

  // Optimistic route guard: a request without a session for a shell route
  // redirects to /enroll (src/proxy.ts) rather than rendering the shell.
  const guarded = await request.get("/chat", { maxRedirects: 0 });
  expect(guarded.status()).toBe(307);
  expect(guarded.headers()["location"]).toMatch(/\/enroll$/);

  // The trusted device resumes without a code...
  await page.getByRole("button", { name: "Bu cihazla devam et" }).click();
  await expect(page).toHaveURL(/\/chat$/);

  // ...until it is forgotten: "Bu cihazı unut" revokes it for good, and the
  // next visit asks for a pairing code again.
  await page.getByRole("link", { name: "Cihazlar" }).click();
  await page.getByRole("button", { name: "Bu cihazı unut" }).click();
  await expect(page).toHaveURL(/\/enroll$/);
  await expect(page.getByLabel("Eşleştirme kodu")).toBeVisible();
});
