import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { HealthPanel } from "@/components/hud/HudPanels";
import { applyHudFrame, parseHudFrame, type HudData, type HudHealth, type HudHomeDirect } from "@/lib/hud/events";

// Phase 13b D11 (ADR-018): the HUD's read-only direct media line.

function health(home_direct?: HudHomeDirect | null): HudHealth {
  return { providers: [], providers_down: 0, budgets: [], budgets_exhausted: 0, preflight: null, home_direct };
}

describe("HUD direct media line (Phase 13b D11)", () => {
  it("shows the brief's line when on", () => {
    render(<HealthPanel health={health({ state: "on", flagged: 1, calls_last_hour: 1 })} mic="closed" stream="open" />);
    const line = screen.getByTestId("hud-home-direct");
    expect(line).toHaveAttribute("data-state", "on");
    expect(screen.getByTestId("hud-home-direct-text").textContent).toBe(
      "Doğrudan medya: açık · 1 cihaz · son 1 saatte 1 çağrı",
    );
  });

  it.each([
    ["off_owner", "kapalı"],
    ["off_env", "kapalı (ortam ayarı)"],
    ["not_configured", "ayarlanmadı"],
  ] as const)("shows %s as text", (state, label) => {
    render(<HealthPanel health={health({ state, flagged: 2, calls_last_hour: 3 })} mic="closed" stream="open" />);
    expect(screen.getByTestId("hud-home-direct")).toHaveAttribute("data-state", state);
    expect(screen.getByTestId("hud-home-direct-text").textContent).toBe(
      `Doğrudan medya: ${label} · 2 cihaz · son 1 saatte 3 çağrı`,
    );
  });

  it("drops the counts when nothing is configured at all", () => {
    render(
      <HealthPanel health={health({ state: "not_configured", flagged: 0, calls_last_hour: 0 })} mic="closed" stream="open" />,
    );
    expect(screen.getByTestId("hud-home-direct-text").textContent).toBe("Doğrudan medya: ayarlanmadı");
  });

  it("offers no toggle: only a link to /connectors", () => {
    render(<HealthPanel health={health({ state: "on", flagged: 1, calls_last_hour: 0 })} mic="closed" stream="open" />);
    const line = screen.getByTestId("hud-home-direct");
    expect(within(line).queryByRole("switch")).toBeNull();
    expect(within(line).queryByRole("checkbox")).toBeNull();
    expect(within(line).queryByRole("button")).toBeNull();
    expect(within(line).getByRole("link", { name: "Ayarlar" })).toHaveAttribute("href", "/connectors");
    // ...and the whole health panel has no control for it either.
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("renders nothing for a backend without the block", () => {
    render(<HealthPanel health={health(undefined)} mic="closed" stream="open" />);
    expect(screen.queryByTestId("hud-home-direct")).toBeNull();
  });

  it("a health delta carries the line", () => {
    const base: HudData = {
      approvals: { pending: 0, items: [] } as unknown as HudData["approvals"],
      tasks: {} as HudData["tasks"],
      cost: {} as HudData["cost"],
      health: health({ state: "on", flagged: 1, calls_last_hour: 0 }),
    };
    const frame = parseHudFrame(
      `data: ${JSON.stringify({ type: "delta", health: health({ state: "off_owner", flagged: 1, calls_last_hour: 2 }) })}`,
    );
    expect(frame).not.toBeNull();
    const next = applyHudFrame(base, frame!);
    expect(next?.health.home_direct).toEqual({ state: "off_owner", flagged: 1, calls_last_hour: 2 });
  });
});
