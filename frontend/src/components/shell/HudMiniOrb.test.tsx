import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { HudMiniOrb } from "@/components/shell/HudMiniOrb";
import { tr } from "@/lib/i18n/tr";
import { setVoiceMuted } from "@/lib/voice/mute";
import { clearVoicePresence, publishVoicePresence } from "@/lib/voice/presence";
import { renderWithClient } from "@/test/render";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ user: { id: "u1" }, loading: false, activeWorkspace: { id: "w1" } }),
}));

function stubSummary(pending: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          workspaces: [{ workspace: "w1", pending_approvals: pending, live_harness_jobs: 0, live_agents: 0 }],
          totals: { pending_approvals: pending, live_harness_jobs: 0, live_agents: 0 },
        }),
        { status: 200 },
      ),
    ),
  );
}

afterEach(() => {
  clearVoicePresence();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("HudMiniOrb (shell header)", () => {
  it("links to /hud and names the state", async () => {
    stubSummary(0);
    renderWithClient(<HudMiniOrb />);
    const orb = screen.getByTestId("hud-mini-orb");
    expect(orb).toHaveAttribute("href", "/hud");
    expect(orb).toHaveAccessibleName(tr.hud.miniOrb(tr.hud.states.idle));
  });

  it("mirrors the global voice state and pending approvals", async () => {
    stubSummary(2);
    renderWithClient(<HudMiniOrb />);
    await waitFor(() => expect(screen.getByTestId("hud-mini-orb")).toHaveAttribute("data-state", "awaiting-approval"));
    act(() => publishVoicePresence({ phase: "speaking", heard: "", reply: "", error: null }));
    expect(screen.getByTestId("hud-mini-orb")).toHaveAttribute("data-state", "speaking");
    act(() => setVoiceMuted(true));
    expect(screen.getByTestId("hud-mini-orb")).toHaveAttribute("data-state", "muted");
  });

  it("pulses only for live states, and only with motion allowed", () => {
    stubSummary(0);
    renderWithClient(<HudMiniOrb />);
    const dot = () => screen.getByTestId("hud-mini-orb").firstElementChild!;
    expect(dot().className).not.toContain("animate-pulse");
    act(() => publishVoicePresence({ phase: "listening", heard: "", reply: "", error: null }));
    expect(dot().className).toContain("motion-safe:animate-pulse");
  });
});
