import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GraphView, entitiesPath } from "@/components/memory/GraphView";
import { calledWith } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiGraphEntity } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/memory/graph" }));

const PERSONAL = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const FIRM = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const VIEWER = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const ENT = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";

const { holder } = vi.hoisted(() => ({
  holder: {
    workspaces: [
      { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Kişisel", type: "personal", role: "owner" },
      { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Acme", type: "company", role: "owner" },
      { id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", name: "Başkası", type: "company", role: "viewer" },
    ] as { id: string; name: string; type: string; role: string }[],
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: holder.workspaces[0], workspaces: holder.workspaces }),
}));

const API: ApiGraphEntity = {
  id: ENT,
  workspace: PERSONAL,
  kind: "service",
  name: "api",
  aliases: ["backend"],
  origin: "learned",
  is_private: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const CODE: ApiGraphEntity = { ...API, id: "code-1", kind: "code_module", name: "memory/views.py", origin: "graphify" };

function stub(entities: ApiGraphEntity[] = [API, CODE]) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input) => {
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (input.startsWith("/api/memory/graph/entities?")) return json(entities);
    if (input === `/api/memory/graph/entities/${ENT}`) {
      return json({ entity: API, edges: [], fact_count: 0, code_graph: null });
    }
    if (input.startsWith(`/api/memory/graph/entities/${ENT}/facts`)) return json([]);
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("entitiesPath", () => {
  it("encodes the search and the kind filter", () => {
    expect(entitiesPath(PERSONAL, "", "")).toBe(`/api/memory/graph/entities?workspace=${PERSONAL}`);
    expect(entitiesPath(PERSONAL, "a&b", "tool")).toBe(
      `/api/memory/graph/entities?workspace=${PERSONAL}&q=a%26b&kind=tool`,
    );
  });
});

describe("GraphView", () => {
  it("lists entities as an accessible list, Graphify ones labelled", async () => {
    stub();
    renderWithClient(<GraphView />);
    const list = await screen.findByRole("list", { name: "Varlıklar" });
    const items = within(list).getAllByRole("button");
    expect(items).toHaveLength(2);
    expect(items[1]).toHaveTextContent("Graphify (salt okunur)");
    expect(screen.getByText("2 varlık")).toBeInTheDocument();
  });

  it("offers only owned workspaces", async () => {
    stub();
    renderWithClient(<GraphView />);
    const select = await screen.findByLabelText("Çalışma alanı");
    const values = within(select).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual([PERSONAL, FIRM]);
    expect(values).not.toContain(VIEWER);
  });

  it("search and kind filter go to the request; switching workspace scopes it", async () => {
    const fetchMock = stub();
    renderWithClient(<GraphView />);
    await screen.findByRole("list", { name: "Varlıklar" });
    await userEvent.type(screen.getByLabelText("Varlık ara"), "api");
    await userEvent.selectOptions(screen.getByLabelText("Tür"), "service");
    await userEvent.click(screen.getByRole("button", { name: "Varlık ara" }));
    await waitFor(() => expect(calledWith(fetchMock, "GET", entitiesPath(PERSONAL, "api", "service"))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Çalışma alanı"), FIRM);
    await waitFor(() => expect(calledWith(fetchMock, "GET", entitiesPath(FIRM, "api", "service"))).toBe(true));
  });

  it("phone width: the selected entity replaces the list, nothing forces overflow", async () => {
    stub();
    const { container } = renderWithClient(<GraphView />);
    const list = await screen.findByRole("list", { name: "Varlıklar" });
    await userEvent.click(within(list).getAllByRole("button")[0]!);
    await screen.findByTestId("entity-timeline");
    // The list column is hidden below `md` and shown beside the page from `md` up.
    const column = list.closest("div.hidden");
    expect(column).not.toBeNull();
    expect(column).toHaveClass("md:block");
    // Wrapping layout only: no fixed pixel width wider than a phone.
    expect(container.innerHTML).not.toMatch(/w-\[\d{3,}px\]/);
  });

  it("empty search result", async () => {
    stub([]);
    renderWithClient(<GraphView />);
    expect(await screen.findByText("Bu aramaya uyan varlık yok.")).toBeInTheDocument();
  });

  it("selecting an entity opens its page; back returns to the list", async () => {
    stub();
    renderWithClient(<GraphView />);
    const list = await screen.findByRole("list", { name: "Varlıklar" });
    const first = within(list).getAllByRole("button")[0]!;
    await userEvent.click(first);
    expect(await screen.findByRole("heading", { name: "api", level: 2 })).toBeInTheDocument();
    expect(first).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("Diğer adlar: backend")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Listeye dön" }));
    expect(screen.queryByTestId("entity-timeline")).not.toBeInTheDocument();
  });

  it("without an owned workspace says so and requests nothing", async () => {
    const saved = holder.workspaces;
    holder.workspaces = [saved[2]!];
    const fetchMock = stub();
    try {
      renderWithClient(<GraphView />);
      expect(screen.getByText(/yalnızca sahibi olduğun/)).toBeInTheDocument();
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      holder.workspaces = saved;
    }
  });
});
