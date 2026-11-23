import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { HealthPanel } from "@/components/hud/HudPanels";
import type { HudHealth, HudNodeLine } from "@/lib/hud/events";

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
