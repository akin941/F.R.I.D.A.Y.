import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EntityTimeline, asOfInstant, splitTimeline } from "@/components/memory/EntityTimeline";
import { calledWith, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiGraphEntity, ApiGraphEntityDetail, ApiGraphFact } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/memory/graph" }));

const WS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ENT = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";

function entity(overrides: Partial<ApiGraphEntity> = {}): ApiGraphEntity {
  return {
    id: ENT,
    workspace: WS,
    kind: "service",
    name: "api",
    aliases: [],
    origin: "learned",
    is_private: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function fact(id: string, overrides: Partial<ApiGraphFact> = {}): ApiGraphFact {
  return {
    id,
    workspace: WS,
    subject: { id: ENT, kind: "service", name: "api" },
    predicate: "deployed_to",
    object_entity: null,
    object_text: "hetzner",
    statement: `api hetzner'e deploy ediliyor (${id})`,
    valid_from: "2026-01-01T00:00:00Z",
    valid_to: null,
    superseded_by: null,
    supersedes: null,
    confidence: 80,
    status: "approved",
    origin: "learned",
    sensitivity: "normal",
    provenance_quote: "we deploy the api on hetzner",
    source_episode_id: null,
    ingestion_source: null,
    in_trash: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const CURRENT = fact("f-current", { statement: "api AWS'e deploy ediliyor", object_text: "aws", valid_from: "2026-03-01T00:00:00Z" });
const OLD = fact("f-old", {
  statement: "api Hetzner'e deploy ediliyordu",
  valid_to: "2026-03-01T00:00:00Z",
  superseded_by: "f-current",
});
const PENDING = fact("f-pending", { status: "pending_approval", statement: "api Postgres kullanıyor", predicate: "uses" });

function stub({
  detail = { entity: entity(), edges: [], fact_count: 4, code_graph: null } as ApiGraphEntityDetail,
  facts = [OLD, CURRENT, PENDING] as ApiGraphFact[],
  asOfFacts = [OLD] as ApiGraphFact[],
} = {}) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input, init) => {
    const method = init?.method ?? "GET";
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    if (input === `/api/memory/graph/entities/${ENT}` && method === "GET") return json(detail);
    if (input.startsWith(`/api/memory/graph/entities/${ENT}/facts?as_of=`)) return json(asOfFacts);
    if (input === `/api/memory/graph/entities/${ENT}/facts`) return json(facts);
    if (method === "DELETE" && input.includes("/facts/")) return new Response(null, { status: 204 });
    if (method === "DELETE") return json({ operation_id: "op", facts: 4 });
    return json(CURRENT);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("splitTimeline / asOfInstant", () => {
  it("splits current, history (newest first) and pending", () => {
    const older = fact("a", { valid_to: "2026-02-01T00:00:00Z" });
    const newer = fact("b", { valid_to: "2026-03-01T00:00:00Z" });
    const parts = splitTimeline([older, newer, CURRENT, PENDING]);
    expect(parts.current.map((f) => f.id)).toEqual(["f-current"]);
    expect(parts.history.map((f) => f.id)).toEqual(["b", "a"]);
    expect(parts.pending.map((f) => f.id)).toEqual(["f-pending"]);
  });

  it("an as-of date covers the whole picked day", () => {
    expect(asOfInstant("2026-03-01")).toBe("2026-03-01T23:59:59");
  });
});

describe("EntityTimeline", () => {
  it("empty: no current fact and no history", async () => {
    stub({ facts: [] });
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    expect(await screen.findByText("Güncel bilgi yok.")).toBeInTheDocument();
    expect(screen.getByText("Geçmiş sürüm yok.")).toBeInTheDocument();
    expect(screen.queryByTestId("facts-pending")).not.toBeInTheDocument();
  });

  it("current only: the fact with its quote and an edit control, no history", async () => {
    stub({ facts: [CURRENT] });
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    const current = await screen.findByTestId("facts-current");
    expect(within(current).getByText("api AWS'e deploy ediliyor")).toBeInTheDocument();
    expect(within(current).getByText(/we deploy the api on hetzner/)).toBeInTheDocument();
    expect(within(current).getByRole("button", { name: "Düzelt" })).toBeInTheDocument();
    expect(screen.getByText("Geçmiş sürüm yok.")).toBeInTheDocument();
  });

  it("with history: the superseded version is readable, closed, and not editable", async () => {
    stub();
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    const history = await screen.findByTestId("facts-history");
    expect(within(history).getByText("api Hetzner'e deploy ediliyordu")).toBeInTheDocument();
    expect(within(history).getByText("2026-01-01 – 2026-03-01")).toBeInTheDocument();
    expect(within(history).queryByRole("button")).not.toBeInTheDocument();
    const pending = screen.getByTestId("facts-pending");
    expect(within(pending).getByText("api Postgres kullanıyor")).toBeInTheDocument();
    expect(within(pending).getByRole("link", { name: "Sabah kartını aç" })).toHaveAttribute("href", "/memory/learning");
  });

  it("Graphify: read-only with the snapshot sha, no edit or trash control", async () => {
    const sha = "0123456789abcdef0123456789abcdef01234567";
    stub({
      detail: {
        entity: entity({ kind: "code_module", name: "memory/views.py", origin: "graphify" }),
        edges: [],
        fact_count: 0,
        code_graph: {
          commit_sha: sha,
          committed_at: "2026-11-01T12:00:00Z",
          imported_at: "2026-11-01T12:05:00Z",
          entity_count: 22,
          fact_count: 30,
          retired_at: null,
        },
      },
      facts: [fact("g1", { status: "system", origin: "graphify", predicate: "imports", statement: "views imports models", provenance_quote: "", confidence: 100 })],
    });
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    const readOnly = await screen.findByTestId("entity-readonly");
    expect(readOnly).toHaveTextContent("0123456789ab");
    expect(await screen.findByText("views imports models")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Düzelt" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Çöpe taşı" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Varlığı çöpe taşı" })).not.toBeInTheDocument();
  });

  it("the as-of date changes the request and shows only the facts valid then", async () => {
    const fetchMock = stub();
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    await screen.findByTestId("facts-current");
    await userEvent.type(screen.getByLabelText("Tarih itibarıyla"), "2026-02-01");
    const asOf = await screen.findByTestId("facts-as-of");
    expect(within(asOf).getByText("api Hetzner'e deploy ediliyordu")).toBeInTheDocument();
    expect(
      calledWith(fetchMock, "GET", `/api/memory/graph/entities/${ENT}/facts?as_of=${encodeURIComponent("2026-02-01T23:59:59")}`),
    ).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Bugüne dön" }));
    expect(await screen.findByTestId("facts-current")).toBeInTheDocument();
  });

  it("edit sends a supersede with the new value and the start date", async () => {
    const fetchMock = stub({ facts: [CURRENT] });
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Düzelt" }));
    const statement = screen.getByLabelText("Bilgi cümlesi");
    await userEvent.clear(statement);
    await userEvent.type(statement, "api GCP'ye deploy ediliyor");
    const value = screen.getByLabelText("Yeni değer");
    await userEvent.clear(value);
    await userEvent.type(value, "gcp");
    const from = screen.getByLabelText("Yeni sürümün başlangıç tarihi");
    await userEvent.clear(from);
    await userEvent.type(from, "2026-04-01");
    await userEvent.click(screen.getByRole("button", { name: "Yeni sürümü kaydet" }));

    await waitFor(() => expect(calledWith(fetchMock, "POST", `/api/memory/graph/facts/f-current/supersede`)).toBe(true));
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/memory/graph/facts/f-current/supersede")!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({
      valid_from: "2026-04-01T00:00:00",
      statement: "api GCP'ye deploy ediliyor",
      object_text: "gcp",
    });
    // Never an in-place PATCH.
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
  });

  it("an object entity is superseded by kind and name", async () => {
    const withEntity = fact("f-e", { object_entity: { id: "x", kind: "tool", name: "hetzner" }, object_text: "" });
    const fetchMock = stub({ facts: [withEntity] });
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Düzelt" }));
    expect(screen.getByLabelText("Yeni değer")).toHaveValue("hetzner");
    await userEvent.click(screen.getByRole("button", { name: "Yeni sürümü kaydet" }));
    await waitFor(() => expect(calledWith(fetchMock, "POST", "/api/memory/graph/facts/f-e/supersede")).toBe(true));
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/memory/graph/facts/f-e/supersede")!;
    expect(JSON.parse(call[1]!.body as string)).toMatchObject({ object_kind: "tool", object_name: "hetzner" });
  });

  it("an edit refused for order shows the reason", async () => {
    const fetchMock = stub({ facts: [CURRENT] });
    fetchMock.mockImplementation(async (input, init) => {
      if ((init?.method ?? "GET") === "POST") {
        return new Response(JSON.stringify({ code: "memory.graph.supersede_order", detail: "x" }), { status: 409 });
      }
      if (input === `/api/memory/graph/entities/${ENT}`) {
        return new Response(JSON.stringify({ entity: entity(), edges: [], fact_count: 1, code_graph: null }));
      }
      return new Response(JSON.stringify([CURRENT]));
    });
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Düzelt" }));
    await userEvent.click(screen.getByRole("button", { name: "Yeni sürümü kaydet" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("fact trash asks first, names the statement, then offers restore", async () => {
    const fetchMock = stub({ facts: [CURRENT] });
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Çöpe taşı" }));
    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("api AWS'e deploy ediliyor");
    expect(calledWith(fetchMock, "DELETE", "/api/memory/graph/facts/f-current")).toBe(false);
    await confirmInDialog("Çöpe taşı");
    await waitFor(() => expect(calledWith(fetchMock, "DELETE", "/api/memory/graph/facts/f-current")).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: "Geri getir" }));
    await waitFor(() => expect(calledWith(fetchMock, "POST", "/api/memory/graph/facts/f-current/restore")).toBe(true));
  });

  it("entity trash confirmation names the entity and its fact count", async () => {
    const fetchMock = stub();
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    await userEvent.click(await screen.findByRole("button", { name: "Varlığı çöpe taşı" }));
    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("“api” çöpe taşınsın mı?");
    expect(dialog).toHaveTextContent("ilgili 4 bilgi");
    await confirmInDialog("Varlığı çöpe taşı");
    await waitFor(() => expect(calledWith(fetchMock, "DELETE", `/api/memory/graph/entities/${ENT}`)).toBe(true));
    expect(await screen.findByTestId("entity-trashed")).toHaveTextContent("“api” ve 4 bilgi çöpe taşındı.");
    await userEvent.click(screen.getByRole("button", { name: "Geri getir" }));
    await waitFor(() => expect(calledWith(fetchMock, "POST", `/api/memory/graph/entities/${ENT}/restore`)).toBe(true));
  });

  it("the timeline is walkable with the arrow keys", async () => {
    const a = fact("h1", { statement: "first", valid_to: "2026-02-01T00:00:00Z" });
    const b = fact("h2", { statement: "second", valid_to: "2026-03-01T00:00:00Z" });
    const c = fact("h3", { statement: "third", valid_to: "2026-04-01T00:00:00Z" });
    stub({ facts: [a, b, c] });
    renderWithClient(<EntityTimeline workspaceId={WS} entityId={ENT} />);
    const list = await screen.findByRole("list", { name: "Geçmiş (yerini başka bilgiye bırakanlar)" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((i) => i.textContent?.slice(0, 5))).toEqual(["third", "secon", "first"]);
    items[0]!.focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(items[1]).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(items[2]).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(items[2]).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(items[0]).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(items[0]).toHaveFocus();
  });
});
