import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IngestionView, ruleTypesFor, sourceName } from "@/components/memory/IngestionView";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiIngestionAudit, ApiIngestionSource } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/memory/ingestion" }));

const PERSONAL = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const FIRM = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const SRC = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const { holder } = vi.hoisted(() => ({
  holder: {
    workspaces: [
      { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Kişisel", type: "personal", role: "owner" },
      { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Acme", type: "company", role: "owner" },
    ],
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: holder.workspaces[0], workspaces: holder.workspaces }),
}));

function source(overrides: Partial<ApiIngestionSource> = {}): ApiIngestionSource {
  return {
    id: SRC,
    workspace: PERSONAL,
    kind: "git",
    enabled: true,
    paused: false,
    paused_at: null,
    daily_token_budget: 40000,
    forgotten_at: null,
    restorable: false,
    today: null,
    ...overrides,
  };
}

function audit(base: ApiIngestionSource): ApiIngestionAudit {
  return {
    ...base,
    counters: [
      {
        day: "2026-11-20",
        stored: 4,
        duplicate: 1,
        dropped_unmapped: 2,
        dropped_paused: 0,
        scrubber_hits: { SECRET: 1 },
        tokens_spent: 300,
        proposals_created: 3,
        proposals_accepted: 2,
        proposals_rejected: 1,
      },
    ],
    episodes: [
      {
        id: "e1",
        workspace: FIRM,
        workspace_name: "Acme",
        kind: "commit",
        occurred_at: "2026-11-20T08:00:00Z",
        owner_text: "fix the deploy script [SECRET]",
        context_text: "3 files changed",
        metadata: {},
        sensitivity: "normal",
        scrub_report: { SECRET: 1 },
        token_estimate: 20,
        expires_at: "2026-12-04T08:00:00Z",
        extracted: false,
      },
    ],
    scrubber_hits: { SECRET: 1 },
    unmapped_keys: [{ hash: "h1", short: "repo:9f…" }],
    tokens_spent_today: 300,
    acceptance: { created: 3, accepted: 2, rejected: 1, rate: 2 / 3 },
    forget_preview: { episodes: 7, memories: 3, core_block_proposals: 1 },
  };
}

function stub(initial: ApiIngestionSource) {
  let current = initial;
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input, init) => {
    const method = init?.method ?? "GET";
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    if (input.startsWith("/api/memory/ingestion/sources?")) return json([current]);
    if (input === `/api/memory/ingestion/sources/${SRC}` && method === "GET") return json(audit(current));
    if (input === `/api/memory/ingestion/sources/${SRC}/rules` && method === "GET") return json([]);
    if (input.startsWith("/api/memory/ingestion/scrub-terms?")) return json([]);
    if (input.endsWith("/forget")) {
      current = { ...current, forgotten_at: "2026-11-20T09:00:00Z", paused: true, restorable: true };
      return json({ operation_id: "op", episodes: 7, core_block_proposals: 1, source: current });
    }
    if (input.endsWith("/restore")) {
      current = { ...current, forgotten_at: null, restorable: false };
      return json({ operation_id: "op", episodes: 7, core_block_proposals: 1, source: current });
    }
    return json({ ok: true }, method === "POST" ? 201 : 200);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openDetails() {
  const card = await screen.findByTestId("ingestion-source-git");
  await userEvent.click(within(card).getByRole("button", { name: "Ayrıntılar" }));
  await screen.findByTestId(`ingestion-detail-${SRC}`);
  return card;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IngestionView", () => {
  it("lists the personal workspace's sources with their state", async () => {
    const fetchMock = stub(source({ paused: true }));
    renderWithClient(<IngestionView />);
    const card = await screen.findByTestId("ingestion-source-git");
    expect(within(card).getByText("Git commit'leri")).toBeInTheDocument();
    expect(within(card).getByText("Duraklatıldı")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0]![0]).toBe(`/api/memory/ingestion/sources?workspace=${PERSONAL}`);
  });

  it("enables, disables and pauses through PATCH", async () => {
    const fetchMock = stub(source({ enabled: false }));
    renderWithClient(<IngestionView />);
    const card = await screen.findByTestId("ingestion-source-git");
    await userEvent.click(within(card).getByRole("button", { name: "Aç" }));
    await userEvent.click(within(card).getByRole("button", { name: "Duraklat" }));
    const patches = fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH");
    expect(patches.map(([, init]) => JSON.parse(init!.body as string))).toEqual([
      { enabled: true },
      { paused: true },
    ]);
  });

  it("shows counters, the last scrubbed items, scrubber hits and unmapped keys", async () => {
    stub(source());
    renderWithClient(<IngestionView />);
    await openDetails();
    expect(screen.getByTestId("ingestion-counters")).toHaveTextContent("2026-11-20");
    expect(screen.getByTestId("ingestion-episodes")).toHaveTextContent("fix the deploy script [SECRET]");
    expect(screen.getByTestId("ingestion-episodes")).toHaveTextContent("Acme");
    expect(screen.getByTestId("scrubber-hits")).toHaveTextContent("SECRET: 1");
    expect(screen.getByTestId("unmapped-keys")).toHaveTextContent("repo:9f…");
    expect(screen.getByText(/Kabul oranı: 67%/)).toBeInTheDocument();
  });

  it("the forget dialog names the source and the counts, and forgets only on confirm", async () => {
    const fetchMock = stub(source());
    renderWithClient(<IngestionView />);
    await openDetails();
    await userEvent.click(screen.getByRole("button", { name: "Bu kaynağı unut" }));
    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("“Git commit'leri” unutulsun mu?");
    expect(dialog).toHaveTextContent("7 kayıt");
    expect(dialog).toHaveTextContent("3 hafıza");
    expect(dialog).toHaveTextContent("1 çekirdek blok önerisi");
    await cancelDialog();
    expect(calledWith(fetchMock, "POST", `/api/memory/ingestion/sources/${SRC}/forget`)).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Bu kaynağı unut" }));
    await confirmInDialog("Bu kaynağı unut");
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/memory/ingestion/sources/${SRC}/forget`)).toBe(true),
    );
  });

  it("restores a forgotten source from its confirmation", async () => {
    const fetchMock = stub(source({ forgotten_at: "2026-11-20T09:00:00Z", paused: true, restorable: true }));
    renderWithClient(<IngestionView />);
    await openDetails();
    expect(screen.queryByRole("button", { name: "Bu kaynağı unut" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Geri getir" }));
    await confirmInDialog("Geri getir");
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/memory/ingestion/sources/${SRC}/restore`)).toBe(true),
    );
  });

  it("adds a mapping rule targeting an owned workspace", async () => {
    const fetchMock = stub(source());
    renderWithClient(<IngestionView />);
    await openDetails();
    const rules = screen.getByTestId("ingestion-rules");
    await userEvent.type(within(rules).getByLabelText("Desen"), "repo-42");
    await userEvent.selectOptions(within(rules).getByLabelText("Hedef çalışma alanı"), FIRM);
    await userEvent.click(within(rules).getByRole("button", { name: "Kural ekle" }));
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/memory/ingestion/sources/${SRC}/rules`)).toBe(true),
    );
    const call = fetchMock.mock.calls.find(
      ([url, init]) => url.endsWith("/rules") && init?.method === "POST",
    )!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({
      rule_type: "repo",
      pattern: "repo-42",
      target_workspace: FIRM,
    });
  });

  it("adds a scrub term to the chosen workspace", async () => {
    const fetchMock = stub(source());
    renderWithClient(<IngestionView />);
    const section = await screen.findByTestId("scrub-terms");
    await userEvent.type(within(section).getByLabelText("Terim"), "Project Falcon");
    await userEvent.click(within(section).getByRole("button", { name: "Terim ekle" }));
    await waitFor(() => expect(calledWith(fetchMock, "POST", "/api/memory/ingestion/scrub-terms")).toBe(true));
    const call = fetchMock.mock.calls.find(([url, init]) => url === "/api/memory/ingestion/scrub-terms" && init?.method === "POST")!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ workspace: PERSONAL, term: "Project Falcon" });
  });
});

describe("Obsidian inbox source (Phase 8B branch 7)", () => {
  it("offers only vault_prefix rules on the inbox source and never elsewhere", () => {
    expect(ruleTypesFor("obsidian_inbox")).toEqual(["vault_prefix"]);
    for (const kind of ["git", "harness", "claude_code_hooks", "calendar"] as const) {
      expect(ruleTypesFor(kind)).not.toContain("vault_prefix");
    }
  });

  it("names the inbox source in Turkish", () => {
    expect(sourceName({ kind: "obsidian_inbox" })).toBe("Obsidian 00_Inbox notları");
  });
});
