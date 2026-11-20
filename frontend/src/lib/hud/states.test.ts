import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  deriveHudState,
  HUD_STATES,
  HUD_VISUALS,
  phaseCaption,
  uniformsFor,
  type HudStateInputs,
} from "@/lib/hud/states";
import { tr } from "@/lib/i18n/tr";

const BASE: HudStateInputs = {
  phase: "idle",
  voiceError: null,
  wakeState: "off",
  muted: false,
  streamLost: false,
  online: true,
  pendingApprovals: 0,
  toolsRunning: 0,
};

const CSS = readFileSync(path.resolve(__dirname, "../../app/globals.css"), "utf8");

function block(selector: string): Record<string, string> {
  const start = CSS.indexOf(`${selector} {`);
  const body = CSS.slice(start, CSS.indexOf("}", start));
  const vars: Record<string, string> = {};
  for (const match of body.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) vars[match[1]!] = match[2]!.trim();
  return vars;
}

describe("HUD states (A8)", () => {
  it("has exactly the eleven states", () => {
    expect(HUD_STATES).toEqual([
      "idle",
      "armed",
      "listening",
      "thinking",
      "tool-running",
      "awaiting-approval",
      "speaking",
      "error",
      "muted",
      "disconnected",
      "offline-local",
    ]);
  });

  it.each(HUD_STATES)("%s has a text label and a visual", (state) => {
    expect(tr.hud.states[state]).toMatch(/\S/);
    expect(HUD_VISUALS[state].token).toBe(`--hud-${state}`);
  });

  it("every state is textually distinct", () => {
    const labels = HUD_STATES.map((s) => tr.hud.states[s]);
    expect(new Set(labels).size).toBe(HUD_STATES.length);
  });

  it.each([
    [":root", "light"],
    [".dark", "dark"],
  ])("every state has its own colour token in the %s palette", (selector) => {
    const palette = block(selector);
    const values = HUD_STATES.map((s) => palette[`hud-${s}`]);
    for (const value of values) expect(value).toMatch(/^oklch\(/);
    expect(new Set(values).size).toBe(HUD_STATES.length);
  });

  it("maps every state to distinct shader colours and a distinct uniform set", () => {
    const colours = HUD_STATES.map((s) => HUD_VISUALS[s].glColors.join());
    expect(new Set(colours).size).toBe(HUD_STATES.length);
    const sets = HUD_STATES.map((s) => JSON.stringify(uniformsFor(s, 0)));
    expect(new Set(sets).size).toBe(HUD_STATES.length);
    for (const state of HUD_STATES) {
      const u = uniformsFor(state, 0);
      expect(u.color1).toMatch(/^#[0-9a-f]{6}$/);
      expect(u.color2).toMatch(/^#[0-9a-f]{6}$/);
      for (const n of [u.input, u.output]) {
        expect(n).toBeGreaterThanOrEqual(0);
        expect(n).toBeLessThanOrEqual(1);
      }
    }
  });

  it("drives listening and speaking from the real audio level only", () => {
    expect(uniformsFor("listening", 0.9).input).toBeCloseTo(0.9);
    expect(uniformsFor("speaking", 0.95).output).toBeCloseTo(0.95);
    // Not level-driven: the level is ignored.
    expect(uniformsFor("thinking", 0.9)).toEqual(uniformsFor("thinking", 0));
    expect(uniformsFor("listening", Number.NaN).input).toBe(0.2);
  });

  it("active states are the live ones", () => {
    const active = HUD_STATES.filter((s) => HUD_VISUALS[s].active);
    expect(active).toEqual(["listening", "thinking", "tool-running", "speaking"]);
  });
});

describe("deriveHudState", () => {
  it.each<[Partial<HudStateInputs>, string]>([
    [{}, "idle"],
    [{ wakeState: "armed" }, "armed"],
    [{ wakeState: "paused" }, "armed"],
    [{ wakeState: "error" }, "error"],
    [{ phase: "listening" }, "listening"],
    [{ phase: "finishing" }, "listening"],
    // D10's transcribing shows as thinking (with a caption).
    [{ phase: "transcribing" }, "thinking"],
    [{ phase: "thinking" }, "thinking"],
    [{ phase: "speaking" }, "speaking"],
    [{ phase: "error", voiceError: "voice.stt_failed" }, "error"],
    [{ phase: "error", voiceError: "disconnected" }, "disconnected"],
    [{ streamLost: true }, "disconnected"],
    [{ toolsRunning: 1 }, "tool-running"],
    [{ pendingApprovals: 2 }, "awaiting-approval"],
    [{ online: false }, "offline-local"],
    [{ muted: true }, "muted"],
  ])("%j -> %s", (input, expected) => {
    expect(deriveHudState({ ...BASE, ...input })).toBe(expected);
  });

  it("orders signals: mute > lost stream > voice > work > rest", () => {
    expect(deriveHudState({ ...BASE, muted: true, streamLost: true, phase: "speaking" })).toBe("muted");
    expect(deriveHudState({ ...BASE, streamLost: true, phase: "speaking" })).toBe("disconnected");
    expect(deriveHudState({ ...BASE, phase: "speaking", pendingApprovals: 1 })).toBe("speaking");
    expect(deriveHudState({ ...BASE, toolsRunning: 1, pendingApprovals: 1 })).toBe("tool-running");
    expect(deriveHudState({ ...BASE, pendingApprovals: 1, online: false })).toBe("awaiting-approval");
    expect(deriveHudState({ ...BASE, online: false, wakeState: "armed" })).toBe("offline-local");
  });

  it("captions the transcribing phase", () => {
    expect(phaseCaption("transcribing")).toBe("transcribing");
    expect(tr.hud.captions.transcribing).toContain("yazıya dökülüyor");
    expect(phaseCaption("idle")).toBeNull();
  });
});
