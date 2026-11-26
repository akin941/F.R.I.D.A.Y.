import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RoomsEditor } from "@/components/home/RoomsEditor";
import type { ApiRoom } from "@/types/api";

const WS = "22222222-2222-4222-8222-222222222222";

function room(id: string, name: string, sort: number, aliases: string[] = []): ApiRoom {
  return {
    id,
    name,
    key: name.toLowerCase(),
    aliases,
    sort_order: sort,
    created_at: "2026-11-20T10:00:00Z",
    updated_at: "2026-11-20T10:00:00Z",
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status });
}

function stub(initial: ApiRoom[], handle?: (url: string, init?: RequestInit) => Response | undefined) {
  const state = { rooms: initial };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const handled = handle?.(url, init);
    if (handled) return handled;
    const method = init?.method ?? "GET";
    if (method === "GET") {
      return jsonResponse({
        workspace: WS,
        limits: { aliases: 5, alias_min_chars: 2, alias_max_chars: 40, name_max_chars: 60 },
        results: state.rooms,
      });
    }
    if (method === "POST") {
      const body = JSON.parse(init!.body as string);
      const created = room(`id-${state.rooms.length}`, body.name, state.rooms.length, body.aliases);
      state.rooms = [...state.rooms, created];
      return jsonResponse(created, 201);
    }
    if (method === "PATCH") return jsonResponse({});
    if (method === "DELETE") return jsonResponse(null, 204);
    return jsonResponse({}, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const calls = (fetchMock: ReturnType<typeof stub>) =>
  fetchMock.mock.calls.map(([url, init]) => [init?.method ?? "GET", url, init?.body ?? null]);

describe("RoomsEditor (Phase 13 D2)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists rooms and adds one with comma-separated aliases", async () => {
    const fetchMock = stub([room("a", "Salon", 0, ["oturma odası"])]);
    const user = userEvent.setup();
    render(<RoomsEditor workspaceId={WS} />);
    expect(await screen.findByDisplayValue("Salon")).toBeInTheDocument();
    expect(screen.getByDisplayValue("oturma odası")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Oda adı", { selector: "#room-new-name" }), "Çalışma odası");
    await user.type(screen.getByLabelText("Takma adlar (virgülle)"), "çalışma,  ofis ,");
    await user.click(screen.getByRole("button", { name: "Ekle" }));
    await screen.findByDisplayValue("Çalışma odası");
    const post = calls(fetchMock).find(([method]) => method === "POST")!;
    expect(post[1]).toBe(`/api/workspaces/${WS}/rooms`);
    expect(JSON.parse(post[2] as string)).toEqual({ name: "Çalışma odası", aliases: ["çalışma", "ofis"] });
  });

  it("saves a rename, moves rooms by swapping their order", async () => {
    const fetchMock = stub([room("a", "Salon", 0), room("b", "Mutfak", 1)]);
    const user = userEvent.setup();
    render(<RoomsEditor workspaceId={WS} />);
    const rows = await screen.findAllByTestId("room-row");
    const nameInput = within(rows[0]!).getByLabelText("Oda adı");
    await user.clear(nameInput);
    await user.type(nameInput, "Büyük salon");
    await user.click(within(rows[0]!).getByRole("button", { name: "Kaydet" }));
    await user.click(screen.getByRole("button", { name: "Mutfak odasını yukarı taşı" }));
    await waitFor(() =>
      expect(calls(fetchMock).filter(([method]) => method === "PATCH")).toEqual([
        ["PATCH", `/api/workspaces/${WS}/rooms/a`, JSON.stringify({ name: "Büyük salon", aliases: [] })],
        ["PATCH", `/api/workspaces/${WS}/rooms/b`, JSON.stringify({ sort_order: 0 })],
        ["PATCH", `/api/workspaces/${WS}/rooms/a`, JSON.stringify({ sort_order: 1 })],
      ]),
    );
    expect(screen.getByRole("button", { name: "Salon odasını yukarı taşı" })).toBeDisabled();
  });

  it("shows the backend's Turkish message for a conflict", async () => {
    stub([room("a", "Salon", 0)], (url, init) =>
      init?.method === "POST"
        ? jsonResponse({ detail: "taken", code: "label.conflict" }, 409)
        : undefined,
    );
    const user = userEvent.setup();
    render(<RoomsEditor workspaceId={WS} />);
    await screen.findByDisplayValue("Salon");
    await user.type(screen.getByLabelText("Oda adı", { selector: "#room-new-name" }), "SALON");
    await user.click(screen.getByRole("button", { name: "Ekle" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("zaten kullanılıyor");
  });

  it("deletes behind a confirmation and reports a room in use", async () => {
    const fetchMock = stub([room("a", "Salon", 0)], (url, init) =>
      init?.method === "DELETE"
        ? jsonResponse({ detail: "in use", code: "room.in_use" }, 409)
        : undefined,
    );
    const user = userEvent.setup();
    render(<RoomsEditor workspaceId={WS} />);
    await screen.findByDisplayValue("Salon");
    await user.click(screen.getByRole("button", { name: "Sil" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Sil" }));
    expect(await within(dialog).findByText(/bir Home Assistant varlığına atanmış/)).toBeInTheDocument();
    expect(calls(fetchMock).some(([method]) => method === "DELETE")).toBe(true);
  });
});
