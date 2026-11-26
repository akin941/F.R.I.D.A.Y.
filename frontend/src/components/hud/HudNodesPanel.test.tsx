import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { HudNodesPanel, showNodesPanel } from "@/components/hud/HudNodesPanel";
import type { HudNodePresence, HudNodes } from "@/lib/hud/events";

const QUIET_VOICE = {
  state: "unknown",
  mode: "",
  session_open: false,
  wake_accepts_today: 0,
  wake_discards_today: 0,
  storm_mutes_today: 0,
  at: null,
} as const;

function row(overrides: Partial<HudNodePresence> = {}): HudNodePresence {
  return {
    id: "aaaaaaaa-0000-4000-8000-000000000001",
    name: "MacBook",
    room: { id: "r1", name: "Çalışma odası" },
    role: "laptop",
    platform: "macos",
    status: "connected",
    online: true,
    last_heartbeat_at: new Date().toISOString(),
    last_active_at: null,
    invocable_read_count: 8,
    invocable_action_count: 5,
    voice: { ...QUIET_VOICE },
    ...overrides,
  };
}

function two(desktop: Partial<HudNodePresence> = {}): HudNodes {
  const items = [
    row({ voice: { ...QUIET_VOICE, state: "listening", mode: "always_on", at: new Date().toISOString() } }),
    row({
      id: "aaaaaaaa-0000-4000-8000-000000000002",
      name: "Masaüstü",
      role: "workstation",
      platform: "linux",
      status: "disconnected",
      online: false,
      invocable_action_count: 0,
      ...desktop,
    }),
  ];
  return { items, total: items.length, online: items.filter((item) => item.online).length };
}

describe("HudNodesPanel (Phase 13 D4)", () => {
  it("is shown only with more than one node", () => {
    expect(showNodesPanel(undefined)).toBe(false);
    expect(showNodesPanel({ items: [], total: 0, online: 0 })).toBe(false);
    expect(showNodesPanel({ items: [row()], total: 1, online: 1 })).toBe(false);
    expect(showNodesPanel(two())).toBe(true);
  });

  it("renders one row per node with room, platform, status, counts and mic as text", () => {
    render(<HudNodesPanel nodes={two()} />);
    expect(screen.getByRole("heading", { name: /Düğümler \(1\/2 bağlı\)/ })).toBeInTheDocument();
    const rows = screen.getAllByTestId("hud-nodes-row");
    expect(rows).toHaveLength(2);
    const [mac, desk] = rows as [HTMLElement, HTMLElement];
    expect(mac).toHaveAttribute("href", "/devices/nodes/aaaaaaaa-0000-4000-8000-000000000001");
    expect(within(mac).getByTestId("hud-nodes-room")).toHaveTextContent("Çalışma odası");
    expect(within(mac).getByTestId("hud-nodes-platform")).toHaveTextContent("macOS");
    expect(within(desk).getByTestId("hud-nodes-platform")).toHaveTextContent("Linux");
    // The status word is there, not only a colour.
    expect(within(mac).getByTestId("hud-nodes-status").textContent).toMatch(/^bağlı/);
    expect(within(desk).getByTestId("hud-nodes-status").textContent).toMatch(/^bağlı değil/);
    expect(within(mac).getByTestId("hud-nodes-counts")).toHaveTextContent("Dizüstü · 8 okuma · 5 eylem");
    expect(within(desk).getByTestId("hud-nodes-counts")).toHaveTextContent("8 okuma · 0 eylem");
    expect(within(mac).getByTestId("hud-nodes-mic")).toHaveTextContent("Mikrofon: dinliyor · her zaman açık");
    expect(within(desk).getByTestId("hud-nodes-mic")).toHaveTextContent("Mikrofon: bilinmiyor");
    expect(mac).toHaveAccessibleName("MacBook: bağlı — ayrıntıları aç");
  });

  it("says how many more nodes there are past the cap", () => {
    const nodes = { ...two(), total: 18 };
    render(<HudNodesPanel nodes={nodes} />);
    expect(screen.getByText("+16 düğüm daha (Cihazlar sayfasında)")).toBeInTheDocument();
  });

  it("announces only connect / disconnect changes", () => {
    const view = render(<HudNodesPanel nodes={two()} />);
    const live = screen.getByTestId("hud-nodes-announcement");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveTextContent("");
    // A heartbeat / count change is not announced.
    view.rerender(<HudNodesPanel nodes={two({ invocable_read_count: 3, last_heartbeat_at: null })} />);
    expect(live).toHaveTextContent("");
    view.rerender(<HudNodesPanel nodes={two({ status: "connected", online: true })} />);
    expect(live).toHaveTextContent("Masaüstü bağlandı");
    view.rerender(<HudNodesPanel nodes={two({ status: "disconnected", online: false })} />);
    expect(live).toHaveTextContent("Masaüstü bağlantısı kesildi");
    // Only the panel's live region is polite; rows are not live.
    for (const element of screen.getByTestId("hud-nodes").querySelectorAll("[aria-live]")) {
      expect(element).toBe(live);
    }
  });

  it("has no axe violations", async () => {
    const { container } = render(<HudNodesPanel nodes={two({ status: "kill_switch", online: false })} />);
    const results = await axe.run(container, {
      // jsdom has no layout: colour contrast is checked by tokens.test.ts.
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });
});
