import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LearningCardView } from "@/components/memory/LearningCardView";
import { calledWith, confirmInDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiLearningDigest, ApiLearningItem } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/memory/learning" }));

const M1 = "11111111-1111-4111-8111-111111111111";
const M2 = "22222222-2222-4222-8222-222222222222";
const M3 = "33333333-3333-4333-8333-333333333333";
const P1 = "44444444-4444-4444-8444-444444444444";

function memoryItem(overrides: Partial<ApiLearningItem> = {}): ApiLearningItem {
  return {
    kind: "memory",
    id: M1,
    title: "Prefers small PRs",
    content: "The owner prefers small, reviewable PRs.",
    quote: "I want small PRs that I can review",
    memory_type: "preference",
    sensitivity: "normal",
    source_kind: "git",
    created_at: "2026-11-20T03:00:00Z",
    bulk_approvable: false,
    actions: ["approve", "edit", "reject"],
    ...overrides,
  } as ApiLearningItem;
}

function coreBlockItem(): ApiLearningItem {
  return {
    kind: "core-block",
    id: P1,
    label: "current_focus",
    title: "current_focus",
    content: "Phase 8A learning UI",
    diff: "+ Phase 8A learning UI",
    base_version: 2,
    quote: "this week I am finishing the learning UI",
    inferred: true,
    source_kind: "harness",
    created_at: "2026-11-20T03:00:00Z",
    bulk_approvable: false,
    actions: ["approve", "reject"],
  };
}

function digest(items: ApiLearningItem[], firm: ApiLearningItem[] = []): ApiLearningDigest {
  const workspaces: ApiLearningDigest["workspaces"] = [
    { workspace: { id: "w-personal", name: "Kişisel", type: "personal" }, items },
  ];
  if (firm.length) workspaces.push({ workspace: { id: "w-firm", name: "Acme", type: "company" }, items: firm });
  return { since: "2026-11-19T00:00:00Z", count: items.length + firm.length, workspaces };
}

function stub(body: ApiLearningDigest) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input) => {
    if (input === "/api/memory/learning/digest") return new Response(JSON.stringify(body), { status: 200 });
    return new Response(JSON.stringify({ id: "x" }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LearningCardView", () => {
  it("groups proposals by workspace and always renders the provenance quote", async () => {
    stub(digest([memoryItem(), coreBlockItem()], [memoryItem({ id: M2, title: "Acme deploy day", quote: "" })]));
    renderWithClient(<LearningCardView />);

    expect(await screen.findByText("Prefers small PRs")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Kişisel" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Acme" })).toBeInTheDocument();
    const first = screen.getByTestId(`learning-item-${M1}`);
    expect(within(first).getByTestId("provenance-quote")).toHaveTextContent("I want small PRs that I can review");
    expect(within(screen.getByTestId(`learning-item-${P1}`)).getByTestId("provenance-quote")).toBeInTheDocument();
    // A missing quote is called out, never silently omitted.
    expect(within(screen.getByTestId(`learning-item-${M2}`)).getByTestId("quote-missing")).toBeInTheDocument();
    expect(within(first).getByText(/Git commit'leri/)).toBeInTheDocument();
  });

  it("approves a memory proposal through its own endpoint and removes it", async () => {
    const fetchMock = stub(digest([memoryItem()]));
    renderWithClient(<LearningCardView />);
    const card = await screen.findByTestId(`learning-item-${M1}`);
    await userEvent.click(within(card).getByRole("button", { name: "Onayla" }));
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/memory/learning/proposals/memory/${M1}/approve`)).toBe(true),
    );
  });

  it("core-block proposals: approve per item, no edit control, no body", async () => {
    const fetchMock = stub(digest([coreBlockItem()]));
    renderWithClient(<LearningCardView />);
    const card = await screen.findByTestId(`learning-item-${P1}`);
    expect(within(card).queryByRole("button", { name: "Düzenle" })).not.toBeInTheDocument();
    expect(within(card).getByText("+ Phase 8A learning UI")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Onayla" }));
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/memory/learning/proposals/core-block/${P1}/approve`)).toBe(true),
    );
    const call = fetchMock.mock.calls.find(([url]) => url.endsWith("/approve"))!;
    expect(call[1]?.body).toBeUndefined();
  });

  it("rejects only from the confirmation dialog", async () => {
    const fetchMock = stub(digest([memoryItem()]));
    renderWithClient(<LearningCardView />);
    const card = await screen.findByTestId(`learning-item-${M1}`);
    await userEvent.click(within(card).getByRole("button", { name: "Reddet" }));
    expect(calledWith(fetchMock, "POST", `/api/memory/learning/proposals/memory/${M1}/reject`)).toBe(false);
    await confirmInDialog("Reddet");
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/memory/learning/proposals/memory/${M1}/reject`)).toBe(true),
    );
  });

  it("edits a memory proposal with curated fields", async () => {
    const fetchMock = stub(digest([memoryItem()]));
    renderWithClient(<LearningCardView />);
    const card = await screen.findByTestId(`learning-item-${M1}`);
    await userEvent.click(within(card).getByRole("button", { name: "Düzenle" }));
    const title = within(card).getByLabelText("Başlık");
    await userEvent.clear(title);
    await userEvent.type(title, "Small PRs");
    await userEvent.click(within(card).getByRole("button", { name: "Kaydet" }));
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/memory/learning/proposals/memory/${M1}/edit`)).toBe(true),
    );
    const call = fetchMock.mock.calls.find(([url]) => url.endsWith("/edit"))!;
    expect(JSON.parse(call[1]!.body as string)).toMatchObject({ title: "Small PRs" });
  });

  it("offers no bulk control for non-work_log items", async () => {
    stub(digest([memoryItem(), memoryItem({ id: M2 }), coreBlockItem()]));
    renderWithClient(<LearningCardView />);
    await screen.findByTestId(`learning-item-${M1}`);
    expect(screen.queryByTestId("bulk-work-log")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("bulk-approves only the work_log items", async () => {
    const workLog = { memory_type: "work_log", bulk_approvable: true } as Partial<ApiLearningItem>;
    const fetchMock = stub(
      digest([memoryItem({ id: M1, ...workLog }), memoryItem({ id: M2, ...workLog }), memoryItem({ id: M3 })]),
    );
    renderWithClient(<LearningCardView />);
    const bulk = await screen.findByTestId("bulk-work-log");
    await userEvent.click(within(bulk).getByRole("button", { name: "2 günlük kaydını birlikte onayla" }));
    await confirmInDialog("Onayla");
    await waitFor(() => expect(calledWith(fetchMock, "POST", "/api/memory/learning/bulk-approve")).toBe(true));
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/memory/learning/bulk-approve")!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ ids: [M1, M2] });
  });

  it("shows the empty state when nothing is pending", async () => {
    stub({ since: "2026-11-19T00:00:00Z", count: 0, workspaces: [] });
    renderWithClient(<LearningCardView />);
    expect(await screen.findByText(/İncelenecek öneri yok/)).toBeInTheDocument();
  });
});
