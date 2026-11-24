import type { VoicePhase } from "@/lib/voice/voice-machine";
import type { WakeState } from "@/lib/voice/wake-mode";

/**
 * The eleven HUD states (Phase 6 brief A8). D10's `transcribing` is shown as
 * *thinking* with a "yazıya dökülüyor" caption; *disconnected* means the HUD
 * event stream or the voice turn connection is lost (Phase 9 adds the node
 * link); *offline-local* means the browser has no internet while Friday's
 * own (local) stack still answers.
 *
 * Every state is distinct three ways: a text label (always rendered, and
 * announced through `aria-live`), a colour token (`--hud-<state>`, checked
 * for contrast in both palettes), and a motion pattern / shader uniform set
 * (static under reduced motion -- the colour and the text then carry it).
 */
export const HUD_STATES = [
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
] as const;

export type HudState = (typeof HUD_STATES)[number];

export interface HudStateInputs {
  phase: VoicePhase;
  voiceError: string | null;
  wakeState: WakeState;
  muted: boolean;
  /** The HUD event stream is down (not merely reconnecting on schedule). */
  streamLost: boolean;
  /** `navigator.onLine`. */
  online: boolean;
  pendingApprovals: number;
  toolsRunning: number;
  /**
   * Phase 11 D15: the Mac node's mic state (the HUD stream's
   * `health.node.voice.state`). Shown on the ring only while no browser
   * voice turn is active; absent on a backend predating it.
   */
  nodeVoice?: string | null;
}

/** The node mic states the ring shows (D15), when the browser is idle. */
const NODE_RING_STATES: Readonly<Record<string, HudState>> = {
  listening: "listening",
  thinking: "thinking",
  speaking: "speaking",
};

/** Voice error codes that mean the turn connection itself was lost. */
const CONNECTION_ERRORS = new Set(["disconnected", "network", "stream_lost"]);

/**
 * One state from all signals, by priority: the owner's mute first (nothing
 * can be heard or said), then a lost connection, a voice error, the live
 * voice phases, then the background work signals, then the resting states.
 */
export function deriveHudState(input: HudStateInputs): HudState {
  if (input.muted) return "muted";
  if (input.streamLost) return "disconnected";
  if (input.phase === "error") {
    return input.voiceError && CONNECTION_ERRORS.has(input.voiceError) ? "disconnected" : "error";
  }
  if (input.phase === "listening" || input.phase === "finishing") return "listening";
  if (input.phase === "transcribing" || input.phase === "thinking") return "thinking";
  if (input.phase === "speaking") return "speaking";
  const fromNode =
    input.phase === "idle" && input.nodeVoice ? NODE_RING_STATES[input.nodeVoice] : undefined;
  if (fromNode) return fromNode;
  if (input.toolsRunning > 0) return "tool-running";
  if (input.pendingApprovals > 0) return "awaiting-approval";
  if (!input.online) return "offline-local";
  if (input.wakeState === "error") return "error";
  if (input.wakeState === "armed" || input.wakeState === "paused" || input.wakeState === "capturing") {
    return "armed";
  }
  return "idle";
}

/** The canvas fallback's motion pattern for a state. */
export type HudPattern = "breathe" | "level" | "spin" | "orbit" | "pulse" | "flicker" | "still";

export interface HudVisual {
  /** CSS custom property (globals.css, both palettes). */
  token: `--hud-${HudState}`;
  /** Shader colours [darker, lighter] (three.js cannot parse oklch()). */
  glColors: readonly [string, string];
  /** Shader targets: mic-side and output-side energy 0..1. */
  input: number;
  output: number;
  /** Animation speed multiplier. */
  speed: number;
  pattern: HudPattern;
  /** Active states animate at the active frame cap; others at the idle cap. */
  active: boolean;
  /** Whether the live audio level drives the orb in this state. */
  levelDriven: boolean;
}

export const HUD_VISUALS: Record<HudState, HudVisual> = {
  idle: {
    token: "--hud-idle",
    glColors: ["#5b6170", "#9aa3b5"],
    input: 0,
    output: 0.25,
    speed: 0.3,
    pattern: "breathe",
    active: false,
    levelDriven: false,
  },
  armed: {
    token: "--hud-armed",
    glColors: ["#0f766e", "#5eead4"],
    input: 0.15,
    output: 0.3,
    speed: 0.45,
    pattern: "breathe",
    active: false,
    levelDriven: false,
  },
  listening: {
    token: "--hud-listening",
    glColors: ["#4338ca", "#a5b4fc"],
    input: 0.6,
    output: 0.45,
    speed: 1,
    pattern: "level",
    active: true,
    levelDriven: true,
  },
  thinking: {
    token: "--hud-thinking",
    glColors: ["#6d28d9", "#c4b5fd"],
    input: 0.38,
    output: 0.5,
    speed: 1.2,
    pattern: "spin",
    active: true,
    levelDriven: false,
  },
  "tool-running": {
    token: "--hud-tool-running",
    glColors: ["#1d4ed8", "#93c5fd"],
    input: 0.3,
    output: 0.65,
    speed: 1.6,
    pattern: "orbit",
    active: true,
    levelDriven: false,
  },
  "awaiting-approval": {
    token: "--hud-awaiting-approval",
    glColors: ["#b45309", "#fcd34d"],
    input: 0.2,
    output: 0.4,
    speed: 0.6,
    pattern: "pulse",
    active: false,
    levelDriven: false,
  },
  speaking: {
    token: "--hud-speaking",
    glColors: ["#047857", "#6ee7b7"],
    input: 0.65,
    output: 0.8,
    speed: 1.1,
    pattern: "level",
    active: true,
    levelDriven: true,
  },
  error: {
    token: "--hud-error",
    glColors: ["#b91c1c", "#fca5a5"],
    input: 0.1,
    output: 0.35,
    speed: 0.5,
    pattern: "flicker",
    active: false,
    levelDriven: false,
  },
  muted: {
    token: "--hud-muted",
    glColors: ["#3f3f46", "#71717a"],
    input: 0,
    output: 0.1,
    speed: 0,
    pattern: "still",
    active: false,
    levelDriven: false,
  },
  disconnected: {
    token: "--hud-disconnected",
    glColors: ["#be185d", "#f9a8d4"],
    input: 0,
    output: 0.2,
    speed: 0.2,
    pattern: "flicker",
    active: false,
    levelDriven: false,
  },
  "offline-local": {
    token: "--hud-offline-local",
    glColors: ["#4d7c0f", "#bef264"],
    input: 0.1,
    output: 0.3,
    speed: 0.35,
    pattern: "breathe",
    active: false,
    levelDriven: false,
  },
};

export interface HudUniforms {
  color1: string;
  color2: string;
  input: number;
  output: number;
  speed: number;
}

function clamp01(n: number) {
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0;
}

/**
 * The shader targets for a state. In the level-driven states the live audio
 * level replaces the synthetic energy (D10: real signals, no jitter).
 */
export function uniformsFor(state: HudState, level = 0): HudUniforms {
  const visual = HUD_VISUALS[state];
  const live = clamp01(level);
  return {
    color1: visual.glColors[0],
    color2: visual.glColors[1],
    input: visual.levelDriven ? Math.max(0.2, live) : visual.input,
    output: visual.levelDriven && state === "speaking" ? Math.max(0.4, live) : visual.output,
    speed: visual.speed,
  };
}

/** The live caption under the state label (D10 `transcribing` caption). */
export function phaseCaption(phase: VoicePhase): "transcribing" | "finishing" | null {
  if (phase === "transcribing") return "transcribing";
  if (phase === "finishing") return "finishing";
  return null;
}
