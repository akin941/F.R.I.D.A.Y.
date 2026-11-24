import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { HealthPanel } from "@/components/hud/HudPanels";
import type { HudHealth, HudNodeLine, HudNodeVoice } from "@/lib/hud/events";

function health(node?: HudNodeLine | null): HudHealth {
  return { providers: [], providers_down: 0, budgets: [], budgets_exhausted: 0, preflight: null, node };
}

function line(overrides: Partial<HudNodeLine> = {}): HudNodeLine {
  return {
    status: "connected",
    last_heartbeat_at: new Date().toISOString(),
    catalogue_sha256: "0123456789ab",
    invocable_count: 7,
    advertised_count: 8,
    spool_depth: 0,
    node_version: "0.1.0",
    ...overrides,
  };
}

describe("HUD health panel node line (Phase 9 D11)", () => {
  it.each([
    ["connected", "bağlı"],
    ["disconnected", "bağlı değil"],
    ["kill_switch", "acil durdurma açık"],
    ["revoked", "iptal edildi"],
    ["credential_expiring", "kimlik bilgisi yenilenmeli"],
    ["never_paired", "eşleştirilmedi"],
  ] as const)("shows %s as text", (status, label) => {
    render(<HealthPanel health={health(line({ status }))} mic="closed" stream="open" />);
    const node = screen.getByTestId("hud-node");
    expect(node).toHaveAttribute("data-status", status);
    expect(screen.getByTestId("hud-node-status").textContent).toMatch(new RegExp(`^${label}`));
  });

  it("shows counts, spool depth and version for a paired node", () => {
    render(<HealthPanel health={health(line({ spool_depth: 4 }))} mic="closed" stream="open" />);
    const node = screen.getByTestId("hud-node");
    expect(node).toHaveTextContent("7/8 yetenek");
    expect(node).toHaveTextContent("kuyrukta 4 olay");
    expect(node).toHaveTextContent("v0.1.0");
    expect(node).toHaveTextContent("son sinyal");
    expect(screen.getByRole("link", { name: "Yönet" })).toHaveAttribute("href", "/devices");
  });

  it("shows no counts when never paired", () => {
    render(
      <HealthPanel
        health={health(line({ status: "never_paired", last_heartbeat_at: null, advertised_count: 0, invocable_count: 0 }))}
        mic="closed"
        stream="open"
      />,
    );
    expect(screen.getByTestId("hud-node")).not.toHaveTextContent("yetenek");
  });

  it("renders nothing for a backend without the node block", () => {
    render(<HealthPanel health={health(undefined)} mic="closed" stream="open" />);
    expect(screen.queryByTestId("hud-node")).not.toBeInTheDocument();
  });
});

function voice(overrides: Partial<HudNodeVoice> = {}): HudNodeVoice {
  return {
    state: "listening",
    mode: "always_on",
    session_open: true,
    wake_accepts_today: 5,
    wake_discards_today: 2,
    storm_mutes_today: 0,
    at: new Date().toISOString(),
    ...overrides,
  };
}

describe("HUD node line mic state (Phase 11 D15)", () => {
  it.each([
    ["off", "kapalı"],
    ["armed_ptt", "bas-konuş bekliyor"],
    ["armed_wake", "uyandırma kelimesini bekliyor"],
    ["listening", "dinliyor"],
    ["thinking", "düşünüyor"],
    ["speaking", "konuşuyor"],
    ["muted", "susturuldu"],
    ["storm_muted", "uyandırma fırtınası"],
    ["error", "hata"],
    ["unknown", "bilinmiyor"],
  ] as const)("shows %s as text", (state, label) => {
    render(<HealthPanel health={health(line({ voice: voice({ state }) }))} mic="closed" stream="open" />);
    const mic = screen.getByTestId("hud-node-mic");
    expect(mic).toHaveAttribute("data-state", state);
    expect(mic).toHaveTextContent(`Mac mikrofonu: ${label}`);
  });

  it("shows mode, session and the daily counters for a live state", () => {
    render(
      <HealthPanel health={health(line({ voice: voice({ storm_mutes_today: 1 }) }))} mic="closed" stream="open" />,
    );
    const mic = screen.getByTestId("hud-node-mic");
    expect(mic).toHaveTextContent("her zaman açık");
    expect(mic).toHaveTextContent("oturum açık");
    expect(screen.getByTestId("hud-node-mic-counts")).toHaveTextContent("bugün 5 uyanma · 2 elendi · 1 fırtına");
  });

  it("shows no mode or counters when the state is unknown or off", () => {
    for (const state of ["unknown", "off"] as const) {
      const { unmount } = render(
        <HealthPanel health={health(line({ voice: voice({ state }) }))} mic="closed" stream="open" />,
      );
      expect(screen.queryByTestId("hud-node-mic-counts")).not.toBeInTheDocument();
      expect(screen.getByTestId("hud-node-mic")).not.toHaveTextContent("her zaman açık");
      unmount();
    }
  });

  it("renders no mic line for a never-paired node or an older backend", () => {
    const { unmount } = render(
      <HealthPanel
        health={health(line({ status: "never_paired", voice: voice({ state: "unknown" }) }))}
        mic="closed"
        stream="open"
      />,
    );
    expect(screen.queryByTestId("hud-node-mic")).not.toBeInTheDocument();
    unmount();
    render(<HealthPanel health={health(line())} mic="closed" stream="open" />);
    expect(screen.queryByTestId("hud-node-mic")).not.toBeInTheDocument();
  });
});
