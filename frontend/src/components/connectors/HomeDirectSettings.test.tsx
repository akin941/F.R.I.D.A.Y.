import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HomeDirectSettings } from "@/components/connectors/HomeDirectSettings";
import type { ApiHomeDirectCall, ApiHomeDirectSettings as Settings } from "@/types/api";

// Phase 13b D6/D11 (ADR-018): the direct media panel on /connectors.

const WS = "22222222-2222-4222-8222-222222222222";
const CLOSED = [
  "media_pause",
  "media_play",
  "media_play_pause",
  "media_stop",
  "volume_down",
  "volume_mute",
  "volume_set",
  "volume_up",
];

function settings(overrides: Partial<Settings> = {}): Settings {
  return {
    workspace: WS,
    kill_switch: false,
    env_kill_switch: false,
    state: "on",
    volume_max: 0.6,
    rate_global_per_minute: 30,
    rate_entity_per_minute: 10,
    services: CLOSED,
    digest: "9ca85ee2",
    flagged_count: 1,
    ...overrides,
  };
}

function call(overrides: Partial<ApiHomeDirectCall> = {}): ApiHomeDirectCall {
  return {
    id: "c1",
    home_entity_id: "e1",
    entity_id: "media_player.salon_tv",
    service: "media_pause",
    data: {},
    requested_data: null,
    origin: "node",
    node: "n1",
    status: "succeeded",
    error_type: "",
    read_back: true,
    started_at: "2026-11-20T10:00:00Z",
    finished_at: "2026-11-20T10:00:01Z",
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function stub(initial: Settings, calls: ApiHomeDirectCall[] = []) {
  const state = { settings: initial };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET" && url.startsWith("/api/connectors/home-assistant/direct/calls?")) {
      return jsonResponse({ workspace: WS, results: calls });
    }
    if (method === "GET" && url.startsWith("/api/connectors/home-assistant/direct?")) {
      return jsonResponse(state.settings);
    }
    if (method === "PATCH" && url === "/api/connectors/home-assistant/direct") {
      const body = JSON.parse(init!.body as string);
      const next = { ...state.settings, ...body };
      next.state = state.settings.env_kill_switch ? "off_env" : next.kill_switch ? "off_owner" : "on";
      state.settings = next;
      return jsonResponse(next);
    }
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const writes = (fetchMock: ReturnType<typeof stub>) =>
  fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? "GET") !== "GET")
    .map(([url, init]) => [init?.method, url, JSON.parse((init?.body as string) ?? "null")]);

describe("HomeDirectSettings (Phase 13b D11)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the state, the flagged count, the closed list and the limits", async () => {
    stub(settings());
    render(<HomeDirectSettings workspaceId={WS} />);
    const state = await screen.findByTestId("ha-direct-state");
    expect(state).toHaveAttribute("data-state", "on");
    expect(state).toHaveTextContent("Açık");
    expect(screen.getByTestId("ha-direct-flagged")).toHaveTextContent("1 cihaz işaretli");
    const services = within(screen.getByTestId("ha-direct-services")).getAllByRole("listitem");
    expect(services.map((item) => item.querySelector("code")?.textContent)).toEqual(CLOSED);
    expect(screen.getByTestId("ha-direct-limits")).toHaveTextContent("dakikada en fazla 30");
    expect(screen.getByTestId("ha-direct-limits")).toHaveTextContent("cihaz başına 10");
    expect(screen.getByLabelText("Doğrudan ses üst sınırı (%)")).toHaveValue(60);
    expect(screen.queryByTestId("ha-direct-env-notice")).toBeNull();
  });

  it("the kill switch is one PATCH each way", async () => {
    const fetchMock = stub(settings());
    const user = userEvent.setup();
    render(<HomeDirectSettings workspaceId={WS} />);
    const kill = await screen.findByRole("switch", { name: "Doğrudan medyayı kapat" });
    expect(kill).not.toBeChecked();
    await user.click(kill);
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(1));
    expect(writes(fetchMock)[0]).toEqual([
      "PATCH",
      "/api/connectors/home-assistant/direct",
      { workspace: WS, kill_switch: true },
    ]);
    await waitFor(() => expect(screen.getByTestId("ha-direct-state")).toHaveAttribute("data-state", "off_owner"));
    expect(screen.getByRole("switch", { name: "Doğrudan medyayı kapat" })).toBeChecked();
    await user.click(screen.getByRole("switch", { name: "Doğrudan medyayı kapat" }));
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(2));
    expect(writes(fetchMock)[1]![2]).toEqual({ workspace: WS, kill_switch: false });
  });

  it("saves the volume maximum as a fraction and refuses out-of-range values", async () => {
    const fetchMock = stub(settings());
    const user = userEvent.setup();
    render(<HomeDirectSettings workspaceId={WS} />);
    const input = await screen.findByLabelText("Doğrudan ses üst sınırı (%)");
    const save = screen.getByRole("button", { name: "Kaydet" });
    expect(save).toBeDisabled(); // unchanged
    await user.clear(input);
    await user.type(input, "120");
    expect(save).toBeDisabled();
    await user.clear(input);
    await user.type(input, "30");
    await user.click(save);
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(1));
    expect(writes(fetchMock)[0]).toEqual([
      "PATCH",
      "/api/connectors/home-assistant/direct",
      { workspace: WS, volume_max: 0.3 },
    ]);
  });

  it("the env kill switch shows a notice and disables the toggle", async () => {
    stub(settings({ env_kill_switch: true, state: "off_env" }));
    render(<HomeDirectSettings workspaceId={WS} />);
    expect(await screen.findByTestId("ha-direct-env-notice")).toHaveTextContent("HA_DIRECT_MEDIA_KILL_SWITCH");
    expect(screen.getByTestId("ha-direct-state")).toHaveTextContent("ortam ayarı");
    expect(screen.getByRole("switch", { name: "Doğrudan medyayı kapat" })).toBeDisabled();
  });

  it("a zero rate limit reads off_env with its own notice", async () => {
    stub(settings({ state: "off_env", rate_entity_per_minute: 0 }));
    render(<HomeDirectSettings workspaceId={WS} />);
    expect(await screen.findByTestId("ha-direct-env-notice")).toHaveTextContent("sınırı 0");
    expect(screen.getByRole("switch", { name: "Doğrudan medyayı kapat" })).toBeDisabled();
  });

  it("lists the last direct calls with origin, status and a clamped volume", async () => {
    const fetchMock = stub(settings(), [
      call(),
      call({
        id: "c2",
        service: "volume_set",
        data: { volume_level: 0.6 },
        requested_data: { volume_level: 0.9 },
        origin: "voice",
        node: null,
      }),
      call({ id: "c3", status: "unknown_outcome", origin: "chat", node: null }),
    ]);
    render(<HomeDirectSettings workspaceId={WS} />);
    const rows = await screen.findAllByTestId("ha-direct-call");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("media_player.salon_tv");
    expect(rows[0]).toHaveTextContent("duraklat");
    expect(rows[0]).toHaveTextContent("Mac sesi");
    expect(rows[0]).toHaveTextContent("tamam");
    expect(rows[1]).toHaveTextContent("%60");
    expect(within(rows[1]!).getByTestId("ha-direct-call-clamped")).toHaveTextContent("istenen %90");
    expect(rows[2]).toHaveAttribute("data-status", "unknown_outcome");
    expect(rows[2]).toHaveTextContent("sonuç bilinmiyor");
    expect(
      fetchMock.mock.calls.some(([url]) => url === `/api/connectors/home-assistant/direct/calls?workspace=${WS}&limit=20`),
    ).toBe(true);
  });

  it("says when there are no direct calls yet", async () => {
    stub(settings());
    render(<HomeDirectSettings workspaceId={WS} />);
    expect(await screen.findByTestId("ha-direct-calls-empty")).toHaveTextContent("Henüz doğrudan komut yok");
  });

  it("shows a refusal from the backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "x", code: "channel_forbidden" }, 403)),
    );
    render(<HomeDirectSettings workspaceId={WS} />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("refetches when the refresh key changes", async () => {
    const fetchMock = stub(settings());
    const { rerender } = render(<HomeDirectSettings workspaceId={WS} refreshKey={0} />);
    await screen.findByTestId("ha-direct-state");
    const reads = () => fetchMock.mock.calls.filter(([url]) => url.startsWith("/api/connectors/home-assistant/direct?")).length;
    expect(reads()).toBe(1);
    rerender(<HomeDirectSettings workspaceId={WS} refreshKey={1} />);
    await waitFor(() => expect(reads()).toBe(2));
  });
});
