import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import { HudView } from "@/components/hud/HudView";
import type { HudData, HudStreamCallbacks } from "@/lib/hud/events";
import { HUD_STATES, type HudState } from "@/lib/hud/states";
import { tr } from "@/lib/i18n/tr";
import { MUTE_STORAGE_KEY } from "@/lib/voice/mute";
import { clearVoicePresence, publishVoicePresence } from "@/lib/voice/presence";
import type { WakeSnapshot } from "@/lib/voice/wake-mode";

const { workspace, wake } = vi.hoisted(() => ({
  workspace: { id: "11111111-1111-4111-8111-111111111111", name: "Alice", type: "personal", role: "owner" },
  wake: { snapshot: { state: "off", error: null, notice: null } as WakeSnapshot },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspace }),
}));

vi.mock("@/components/voice/VoiceProvider", () => ({
  useVoiceWake: () => ({ snapshot: wake.snapshot }),
}));

// The embedded push-to-talk loop is VoiceView's own (tested there).
vi.mock("@/components/voice/VoiceView", () => ({
  VoiceView: ({ variant }: { variant: string }) => <div data-testid="voice-view-stub" data-variant={variant} />,
}));

// The Phase 8A cards fetch through TanStack Query (tested in HudLearningPanels.test).
vi.mock("@/components/hud/HudLearningPanels", () => ({
  HudSessionsPanel: () => <section data-testid="hud-sessions" data-slot="sessions" />,
  HudLearningPanel: () => <section data-testid="hud-learning" />,
}));

vi.mock("@/components/hud/orb/HudOrbGL", () => ({
  default: () => <div data-testid="hud-orb-gl-stub" />,
}));

function data(overrides: Partial<HudData> = {}): HudData {
  return {
    approvals: { pending_count: 0, items: [], tools_running: 0 },
    tasks: { date: "2026-11-20", open_count: 0, items: [] },
    cost: {
      since: null,
      calls: 3,
      failed_calls: 0,
      input_tokens: 120,
      output_tokens: 40,
      estimated_cost: "0.012300",
      local_only: false,
    },
    health: { providers: [], providers_down: 0, budgets: [], budgets_exhausted: 0, preflight: null },
    ...overrides,
  };
}

function setup() {
  let callbacks: HudStreamCallbacks | null = null;
  const urls: string[] = [];
  const stream = { start: vi.fn(), stop: vi.fn() };
  const factory = vi.fn((url: string, cb: HudStreamCallbacks) => {
    urls.push(url);
    callbacks = cb;
    return stream;
  });
  const view = render(<HudView streamFactory={factory} webglAvailable={() => false} />);
  return {
    ...view,
    urls,
    stream,
    push(next: HudData) {
      act(() => {
        callbacks!.onStatus("open");
        callbacks!.onData(next);
      });
    },
    lose() {
      act(() => callbacks!.onStatus("lost"));
    },
  };
}

function stateOf() {
  return screen.getByTestId("hud").getAttribute("data-state") as HudState;
}

let online = true;

beforeEach(() => {
  online = true;
  vi.spyOn(navigator, "onLine", "get").mockImplementation(() => online);
  wake.snapshot = { state: "off", error: null, notice: null };
  window.localStorage.clear();
});

afterEach(() => {
  clearVoicePresence();
  vi.restoreAllMocks();
});

describe("HudView (A8)", () => {
  it("opens one stream for the active workspace with the client date", () => {
    const h = setup();
    expect(h.urls).toHaveLength(1);
    expect(h.urls[0]).toMatch(/^\/api\/hud\/events\?workspace=11111111-1111-4111-8111-111111111111&date=\d{4}-\d{2}-\d{2}$/);
    expect(h.stream.start).toHaveBeenCalledOnce();
    h.unmount();
    expect(h.stream.stop).toHaveBeenCalledOnce();
  });

  it("embeds the push-to-talk loop in its HUD variant", () => {
    setup();
    expect(screen.getByTestId("voice-view-stub")).toHaveAttribute("data-variant", "hud");
  });

  const SCENARIOS: Record<HudState, (h: ReturnType<typeof setup>) => void> = {
    idle: (h) => h.push(data()),
    armed: (h) => {
      wake.snapshot = { state: "armed", error: null, notice: null };
      h.rerender(<HudView streamFactory={vi.fn(() => ({ start: vi.fn(), stop: vi.fn() }))} webglAvailable={() => false} />);
    },
    listening: () => act(() => publishVoicePresence({ phase: "listening", heard: "", reply: "", error: null })),
    thinking: () => act(() => publishVoicePresence({ phase: "transcribing", heard: "", reply: "", error: null })),
    "tool-running": (h) => h.push(data({ approvals: { pending_count: 0, items: [], tools_running: 1 } })),
    "awaiting-approval": (h) =>
      h.push(
        data({
          approvals: {
            pending_count: 1,
            items: [
              {
                id: "a1",
                tool_name: "vault_export",
                origin: "human",
                requested_channel: "web",
                created_at: new Date().toISOString(),
              },
            ],
            tools_running: 0,
          },
        }),
      ),
    speaking: () => act(() => publishVoicePresence({ phase: "speaking", heard: "", reply: "Tamam.", error: null })),
    error: () => act(() => publishVoicePresence({ phase: "error", heard: "", reply: "", error: "voice.stt_failed" })),
    muted: () => {
      act(() => {
        window.localStorage.setItem(MUTE_STORAGE_KEY, "1");
        window.dispatchEvent(new StorageEvent("storage", { key: MUTE_STORAGE_KEY }));
        window.dispatchEvent(new Event("friday:voice-mute"));
      });
    },
    disconnected: (h) => h.lose(),
    "offline-local": () => {
      online = false;
      act(() => {
        window.dispatchEvent(new Event("offline"));
      });
    },
  };

  it.each(HUD_STATES)("%s: shown as text and announced through aria-live", (state) => {
    const h = setup();
    SCENARIOS[state](h);
    expect(stateOf()).toBe(state);
    const label = tr.hud.states[state];
    expect(screen.getByTestId("hud-state-label")).toHaveTextContent(label);
    const announcement = screen.getByTestId("hud-state-announcement");
    expect(announcement).toHaveAttribute("aria-live", "polite");
    expect(announcement).toHaveTextContent(tr.hud.stateAnnouncement(label));
    // The orb carries the state too (colour / motion).
    expect(screen.getByTestId("hud-orb")).toHaveAttribute("data-state", state);
  });

  it("captions: the transcript, the reply and the transcribing caption", () => {
    setup();
    act(() => publishVoicePresence({ phase: "transcribing", heard: "yarın toplantı", reply: "", error: null }));
    expect(screen.getByTestId("hud-caption-heard")).toHaveTextContent("yarın toplantı");
    expect(screen.getByTestId("hud-caption-phase")).toHaveTextContent("yazıya dökülüyor");
    act(() => publishVoicePresence({ phase: "speaking", heard: "yarın toplantı", reply: "Not aldım.", error: null }));
    expect(screen.getByTestId("hud-caption-reply")).toHaveTextContent("Not aldım.");
  });

  it("approvals are deep links only: no decide control on the HUD (D8)", () => {
    const h = setup();
    SCENARIOS["awaiting-approval"](h);
    const panel = screen.getByTestId("hud-approvals");
    const link = within(panel).getByTestId("hud-approval-link");
    expect(link).toHaveAttribute("href", "/approvals/a1");
    expect(link).toHaveTextContent(tr.approvals.toolTitles.vault_export!);
    expect(within(panel).queryAllByRole("button")).toEqual([]);
    for (const button of screen.queryAllByRole("button")) {
      expect(button.textContent ?? "").not.toMatch(/onayla|reddet|karar/i);
    }
  });

  it("tasks and cost panels render the stream", () => {
    const h = setup();
    h.push(
      data({
        tasks: {
          date: "2026-11-20",
          open_count: 3,
          items: [
            { id: "t1", title: "Faturayı öde", due_date: "2026-11-19", priority: "high", overdue: true },
            { id: "t2", title: "Ali'yi ara", due_date: "2026-11-20", priority: "normal", overdue: false },
          ],
        },
      }),
    );
    const tasks = screen.getByTestId("hud-tasks");
    expect(tasks).toHaveTextContent("Faturayı öde");
    expect(tasks).toHaveTextContent(tr.hud.tasks.overdue);
    expect(tasks).toHaveTextContent(tr.hud.tasks.more(1));
    expect(screen.getByTestId("hud-cost-amount")).toHaveTextContent("$0.0123");
    expect(screen.getByTestId("hud-cost")).toHaveTextContent(tr.hud.cost.calls(3));
  });

  it("health panel: provider-down and budget-exhausted states", () => {
    const h = setup();
    h.push(
      data({
        health: {
          providers: [
            {
              name: "ollama-local",
              capability: "chat",
              status: "down",
              personal_data_allowed: true,
              local: true,
              last_call: { at: new Date().toISOString(), error_type: "timeout" },
            },
            {
              name: "voice-stt-local",
              capability: "stt",
              status: "ok",
              personal_data_allowed: true,
              local: true,
              last_call: null,
            },
          ],
          providers_down: 1,
          budgets: [
            {
              name: "audio-cloud",
              windows: [{ window: "monthly", limit_usd: "5.000000", spent_usd: "5.200000", exhausted: true }],
              exhausted: true,
            },
          ],
          budgets_exhausted: 1,
          preflight: { status: "fail", pass: 9, fail: 1, warn: 2, at: new Date().toISOString(), failed_checks: ["celery"] },
        },
      }),
    );
    const providers = screen.getByTestId("hud-providers");
    const down = within(providers).getByText(/ollama-local/).closest("li")!;
    expect(down).toHaveAttribute("data-status", "down");
    expect(down).toHaveTextContent(tr.hud.health.providerStates.down);
    const budget = within(screen.getByTestId("hud-budgets")).getByText(tr.hud.health.budgetExhausted("audio-cloud"));
    expect(budget.closest("li")).toHaveAttribute("data-exhausted", "true");
    expect(screen.getByTestId("hud-budgets")).toHaveTextContent("$5.20 / $5.00");
    expect(screen.getByTestId("hud-preflight")).toHaveTextContent("başarısız");
    expect(screen.getByTestId("hud-preflight")).toHaveTextContent("celery");
  });

  it("health panel: mic and stream state", () => {
    const h = setup();
    expect(screen.getByTestId("hud-health-mic")).toHaveAttribute("data-mic", "closed");
    expect(screen.getByTestId("hud-health-stream")).toHaveTextContent(tr.hud.health.streamStates.connecting);
    h.lose();
    expect(screen.getByTestId("hud-health-stream")).toHaveTextContent(tr.hud.health.streamStates.lost);
    act(() => publishVoicePresence({ phase: "listening", heard: "", reply: "", error: null }));
    expect(screen.getByTestId("hud-health-mic")).toHaveAttribute("data-mic", "open");
  });

  it("reduced motion: static orb (0 fps, canvas), the state still in text", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) =>
        ({
          matches: query.includes("prefers-reduced-motion"),
          media: query,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
        }) as unknown as MediaQueryList,
    );
    setup();
    act(() => publishVoicePresence({ phase: "speaking", heard: "", reply: "", error: null }));
    expect(screen.getByTestId("hud")).toHaveAttribute("data-fps", "0");
    expect(screen.getByTestId("hud-orb")).toHaveAttribute("data-renderer", "canvas");
    expect(screen.getByTestId("hud-state-label")).toHaveTextContent(tr.hud.states.speaking);
  });

  it("a node action running on the Mac is tool-running, also under reduced motion (Phase 10 D12)", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) =>
        ({
          matches: query.includes("prefers-reduced-motion"),
          media: query,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
        }) as unknown as MediaQueryList,
    );
    let callbacks: HudStreamCallbacks | null = null;
    const terminalStreamFactory = vi.fn(() => ({ start: vi.fn(), stop: vi.fn() }));
    render(
      <HudView
        streamFactory={(_url, cb) => {
          callbacks = cb;
          return { start: vi.fn(), stop: vi.fn() };
        }}
        terminalStreamFactory={terminalStreamFactory}
        webglAvailable={() => false}
      />,
    );
    act(() => {
      callbacks!.onStatus("open");
      callbacks!.onData(
        data({
          terminal: {
            running_count: 1,
            items: [
              {
                id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                capability: "repo.command.run",
                label: "Repoda komut çalıştır",
                class: "gate",
                repo_name: "acme",
                status: "running",
                running: true,
                terminal: false,
                exit_code: null,
                duration_ms: 1000,
                created_at: null,
                finished_at: null,
              },
            ],
          },
        }),
      );
    });
    expect(stateOf()).toBe("tool-running");
    expect(screen.getByTestId("hud")).toHaveAttribute("data-fps", "0");
    expect(screen.getByTestId("hud-state-label")).toHaveTextContent(tr.hud.states["tool-running"]);
    expect(screen.getByTestId("hud-terminal")).toBeInTheDocument();
    expect(terminalStreamFactory).toHaveBeenCalledWith(
      "/api/hud/terminal/dddddddd-dddd-4ddd-8ddd-dddddddddddd/stream",
      expect.anything(),
    );
  });

  it("the ring shows the Mac's live voice while the browser is idle (Phase 11 D15)", () => {
    const h = setup();
    const node = (state: string) =>
      data({
        health: {
          providers: [],
          providers_down: 0,
          budgets: [],
          budgets_exhausted: 0,
          preflight: null,
          node: {
            status: "connected",
            last_heartbeat_at: null,
            catalogue_sha256: "0123456789ab",
            invocable_count: 1,
            advertised_count: 1,
            spool_depth: 0,
            node_version: "0.1.0",
            voice: {
              state: state as "listening",
              mode: "push_to_talk",
              session_open: true,
              wake_accepts_today: 0,
              wake_discards_today: 0,
              storm_mutes_today: 0,
              at: null,
            },
          },
        },
      });
    h.push(node("speaking"));
    expect(stateOf()).toBe("speaking");
    expect(screen.getByTestId("hud-orb")).toHaveAttribute("data-source", "node");
    expect(screen.getByTestId("hud-state-source")).toHaveTextContent("(Mac)");
    expect(screen.getByTestId("hud-node-mic")).toHaveAttribute("data-state", "speaking");
    h.push(node("armed_ptt"));
    expect(stateOf()).toBe("idle");
    expect(screen.getByTestId("hud-orb")).toHaveAttribute("data-source", "browser");
    expect(screen.queryByTestId("hud-state-source")).not.toBeInTheDocument();
  });

  it("frame caps: 30 fps while active, 5 at rest", () => {
    setup();
    expect(screen.getByTestId("hud")).toHaveAttribute("data-fps", "5");
    act(() => publishVoicePresence({ phase: "thinking", heard: "", reply: "", error: null }));
    expect(screen.getByTestId("hud")).toHaveAttribute("data-fps", "30");
  });

  it("renders the Phase 8A sessions panel in its slot and the morning-card summary", () => {
    setup();
    expect(screen.getByTestId("hud-sessions")).toHaveAttribute("data-slot", "sessions");
    expect(screen.getByTestId("hud-learning")).toBeInTheDocument();
  });

  it("remembers the battery and earcon toggles per device", () => {
    setup();
    const battery = screen.getByLabelText(tr.hud.settings.animateOnBattery);
    expect(battery).not.toBeChecked();
    act(() => battery.click());
    expect(JSON.parse(window.localStorage.getItem("friday:hud:settings")!)).toMatchObject({ animateOnBattery: true });
  });
});
