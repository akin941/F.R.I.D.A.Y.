import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HomeEntitiesEditor } from "@/components/connectors/HomeEntitiesEditor";
import type { ApiHomeEntity, ApiHomeEntityCandidate } from "@/types/api";

const WS = "22222222-2222-4222-8222-222222222222";
const MEDIA = [
  "media_pause",
  "media_play",
  "media_play_pause",
  "media_stop",
  "select_source",
  "turn_off",
  "turn_on",
  "volume_down",
  "volume_mute",
  "volume_set",
  "volume_up",
];

function entity(overrides: Partial<ApiHomeEntity> = {}): ApiHomeEntity {
  return {
    id: "e1",
    entity_id: "media_player.salon_tv",
    domain: "media_player",
    label: "TV",
    key: "tv",
    aliases: [],
    services: ["turn_on"],
    enabled: true,
    room: { id: "r1", name: "Salon", key: "salon" },
    created_at: "2026-11-20T10:00:00Z",
    updated_at: "2026-11-20T10:00:00Z",
    ...overrides,
  };
}

function candidate(overrides: Partial<ApiHomeEntityCandidate> = {}): ApiHomeEntityCandidate {
  return {
    entity_id: "media_player.salon_tv",
    domain: "media_player",
    friendly_name: "Samsung QE55",
    state: "off",
    source_list: ["TV", "HDMI 1"],
    services: MEDIA,
    allowlisted: false,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status });
}

function stub(initial: ApiHomeEntity[], candidates: ApiHomeEntityCandidate[] = []) {
  const state = { entities: initial };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET" && url.startsWith("/api/workspaces/")) {
      return jsonResponse({
        workspace: WS,
        limits: {},
        results: [{ id: "r1", name: "Salon", key: "salon", aliases: [], sort_order: 0 }],
      });
    }
    if (method === "GET" && url.startsWith("/api/connectors/home-assistant/entities?")) {
      return jsonResponse({
        workspace: WS,
        limits: { aliases: 5, alias_min_chars: 2, alias_max_chars: 40, label_max_chars: 60 },
        services: { media_player: MEDIA, light: ["turn_off", "turn_on"] },
        results: state.entities,
      });
    }
    if (method === "GET" && url.startsWith("/api/connectors/home-assistant/candidates?")) {
      return jsonResponse({ results: candidates, omitted: 0 });
    }
    if (method === "POST") {
      const body = JSON.parse(init!.body as string);
      const created = entity({ id: "new", entity_id: body.entity_id, label: body.label, services: body.services });
      state.entities = [...state.entities, created];
      return jsonResponse(created, 201);
    }
    if (method === "PATCH") {
      // Phase 13b: a "direct" change shows on the next list read.
      const body = JSON.parse(init!.body as string);
      if ("direct" in body) {
        state.entities = state.entities.map((row) =>
          url.endsWith(`/${row.id}`) ? { ...row, direct: body.direct, updated_at: "2026-11-20T11:00:00Z" } : row,
        );
      }
      return jsonResponse({});
    }
    if (method === "DELETE") return jsonResponse(null, 204);
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const writes = (fetchMock: ReturnType<typeof stub>) =>
  fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? "GET") !== "GET")
    .map(([url, init]) => [init?.method, url, JSON.parse((init?.body as string) ?? "null")]);

describe("HomeEntitiesEditor (Phase 13 D11)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("says an empty allowlist makes nothing callable", async () => {
    stub([]);
    render(<HomeEntitiesEditor workspaceId={WS} />);
    expect(await screen.findByTestId("ha-entities-empty")).toHaveTextContent("hiçbir şey çağrılamaz");
  });

  it("lists entities with room and label and offers only the code-maximum services", async () => {
    stub([entity()]);
    render(<HomeEntitiesEditor workspaceId={WS} />);
    const row = await screen.findByTestId("ha-entity-row");
    expect(row).toHaveTextContent("Salon · TV");
    expect(row).toHaveTextContent("media_player.salon_tv");
    const boxes = within(row).getAllByRole("checkbox");
    expect(boxes).toHaveLength(MEDIA.length);
    expect(within(row).queryByText("toggle")).toBeNull();
    expect(within(row).getByRole("checkbox", { name: /turn_on/ })).toBeChecked();
    expect(within(row).getByRole("checkbox", { name: /volume_mute/ })).not.toBeChecked();
  });

  it("names the Phase 13b play/pause toggle and stop services", async () => {
    stub([entity()]);
    render(<HomeEntitiesEditor workspaceId={WS} />);
    const row = await screen.findByTestId("ha-entity-row");
    const toggle = within(row).getByRole("checkbox", { name: /media_play_pause/ });
    expect(toggle.closest("label")).toHaveTextContent("oynat/duraklat");
    const stop = within(row).getByRole("checkbox", { name: /media_stop/ });
    expect(stop.closest("label")).toHaveTextContent("durdur");
    expect(stop).not.toBeChecked();
  });

  it("saves ticked services and switches an entity off", async () => {
    const fetchMock = stub([entity()]);
    const user = userEvent.setup();
    render(<HomeEntitiesEditor workspaceId={WS} />);
    const row = await screen.findByTestId("ha-entity-row");
    await user.click(within(row).getByRole("checkbox", { name: /volume_mute/ }));
    await user.click(within(row).getByRole("button", { name: "Kaydet" }));
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(1));
    expect(writes(fetchMock)[0]).toEqual([
      "PATCH",
      "/api/connectors/home-assistant/entities/e1",
      { label: "TV", room_id: "r1", aliases: [], services: ["turn_on", "volume_mute"] },
    ]);
    await user.click(within(row).getByRole("button", { name: "Devre dışı bırak" }));
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(2));
    expect(writes(fetchMock)[1]).toEqual([
      "PATCH",
      "/api/connectors/home-assistant/entities/e1",
      { enabled: false },
    ]);
  });

  it("adds an entity only from the live read and only when the owner submits", async () => {
    const fetchMock = stub([], [candidate(), candidate({ entity_id: "light.salon", domain: "light", allowlisted: true })]);
    const user = userEvent.setup();
    render(<HomeEntitiesEditor workspaceId={WS} />);
    await screen.findByTestId("ha-entities-empty");
    await user.click(screen.getByRole("button", { name: "Home Assistant'tan varlıkları oku" }));
    const select = await screen.findByLabelText("Varlık");
    // Already allowlisted entities are not offered again.
    expect(within(select).queryByRole("option", { name: /light\.salon/ })).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
    await user.selectOptions(select, "media_player.salon_tv");
    expect(screen.getByText(/Kaynaklar .*TV, HDMI 1/)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "İzin listesine ekle" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("Etiket"), "TV");
    expect(submit).toBeDisabled(); // no service ticked yet: nothing is allowed by default
    await user.click(screen.getByRole("checkbox", { name: /turn_on/ }));
    await user.selectOptions(screen.getByLabelText("Oda"), "r1");
    await user.click(submit);
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(1));
    expect(writes(fetchMock)[0]).toEqual([
      "POST",
      "/api/connectors/home-assistant/entities",
      {
        workspace: WS,
        entity_id: "media_player.salon_tv",
        label: "TV",
        room_id: "r1",
        aliases: [],
        services: ["turn_on"],
      },
    ]);
    expect(await screen.findByTestId("ha-entity-row")).toHaveTextContent("media_player.salon_tv");
  });

  it("shows a read failure", async () => {
    const fetchMock = stub([]);
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/connectors/home-assistant/candidates")) {
        return jsonResponse({ detail: "x", code: "ha_entity.read_failed" }, 502);
      }
      if (url.startsWith("/api/workspaces/")) return jsonResponse({ results: [] });
      return jsonResponse({ workspace: WS, limits: {}, services: {}, results: [] });
    });
    const user = userEvent.setup();
    render(<HomeEntitiesEditor workspaceId={WS} />);
    await screen.findByTestId("ha-entities-empty");
    await user.click(screen.getByRole("button", { name: "Home Assistant'tan varlıkları oku" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Home Assistant okunamadı");
  });
});

// Phase 13b D5/D11 (ADR-018): the per-entity "Doğrudan (onaysız)" switch.
const ELIGIBLE = {
  services: ["turn_on", "media_pause", "volume_set"],
  direct_services: ["media_pause", "volume_set"],
};

describe("HomeEntitiesEditor direct switch (Phase 13b D11)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is enabled only on an eligible media player and names the services it covers", async () => {
    stub([
      entity({ ...ELIGIBLE, direct: false }),
      entity({ id: "e2", entity_id: "light.salon", domain: "light", label: "Lamba", services: ["turn_on"], direct_services: [] }),
      entity({ id: "e3", entity_id: "media_player.yatak", label: "Yatak TV", services: ["turn_on", "select_source"], direct_services: [] }),
    ]);
    render(<HomeEntitiesEditor workspaceId={WS} />);
    const rows = await screen.findAllByTestId("ha-entity-row");
    const [tv, lamp, bedroom] = rows.map((row) => within(row).getByRole("switch", { name: /Doğrudan/ }));
    expect(tv).toBeEnabled();
    expect(tv).not.toBeChecked();
    expect(within(rows[0]!).getByTestId("ha-direct-help")).toHaveTextContent("duraklat, ses düzeyi");
    expect(lamp).toBeDisabled();
    expect(within(rows[1]!).getByTestId("ha-direct-help")).toHaveTextContent("Yalnızca medya oynatıcılar");
    expect(bedroom).toBeDisabled();
    expect(within(rows[2]!).getByTestId("ha-direct-help")).toHaveTextContent("izinli hizmet yok");
  });

  it("is disabled for a backend without the direct fields", async () => {
    stub([entity({ services: ["media_pause"] })]);
    render(<HomeEntitiesEditor workspaceId={WS} />);
    const row = await screen.findByTestId("ha-entity-row");
    expect(within(row).getByRole("switch", { name: /Doğrudan/ })).toBeDisabled();
  });

  it("turns direct on only after the owner confirms, with one PATCH", async () => {
    const fetchMock = stub([entity({ ...ELIGIBLE, direct: false })]);
    const changed = vi.fn();
    const user = userEvent.setup();
    render(<HomeEntitiesEditor workspaceId={WS} onDirectChanged={changed} />);
    const row = await screen.findByTestId("ha-entity-row");
    await user.click(within(row).getByRole("switch", { name: /Doğrudan/ }));
    // The click alone writes nothing: the confirmation dialog is open.
    expect(writes(fetchMock)).toEqual([]);
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("media_player.salon_tv");
    await user.click(within(dialog).getByRole("button", { name: "Vazgeç" }));
    expect(writes(fetchMock)).toEqual([]);
    await user.click(within(row).getByRole("switch", { name: /Doğrudan/ }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Doğrudan yap" }));
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(1));
    expect(writes(fetchMock)[0]).toEqual(["PATCH", "/api/connectors/home-assistant/entities/e1", { direct: true }]);
    await waitFor(() => expect(screen.getByRole("switch", { name: /Doğrudan/ })).toBeChecked());
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("turns direct off at once", async () => {
    const fetchMock = stub([entity({ ...ELIGIBLE, direct: true })]);
    const changed = vi.fn();
    const user = userEvent.setup();
    render(<HomeEntitiesEditor workspaceId={WS} onDirectChanged={changed} />);
    const row = await screen.findByTestId("ha-entity-row");
    const toggle = within(row).getByRole("switch", { name: /Doğrudan/ });
    expect(toggle).toBeChecked();
    expect(row).toHaveTextContent("Kartsız");
    await user.click(toggle);
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(1));
    expect(writes(fetchMock)[0]).toEqual(["PATCH", "/api/connectors/home-assistant/entities/e1", { direct: false }]);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
  });
});
