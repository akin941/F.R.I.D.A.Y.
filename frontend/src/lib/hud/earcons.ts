import type { HudState } from "@/lib/hud/states";

/**
 * Short earcons synthesized with WebAudio oscillators (Phase 6 A8; no audio
 * assets). Played on a few HUD state transitions only, never while voice is
 * muted (the shell mute is read at play time, fail-closed), never when the
 * owner turned them off for this device, and never before a user gesture
 * has made the AudioContext runnable (autoplay policy: a suspended context
 * just stays silent).
 */

export type Earcon = "listen" | "done" | "error" | "approval";

interface Tone {
  freq: number;
  /** Seconds from the earcon's start. */
  at: number;
  duration: number;
}

export const EARCONS: Record<Earcon, readonly Tone[]> = {
  // Rising pair: the mic opened.
  listen: [
    { freq: 660, at: 0, duration: 0.07 },
    { freq: 880, at: 0.08, duration: 0.09 },
  ],
  // Falling pair: the turn finished.
  done: [
    { freq: 880, at: 0, duration: 0.07 },
    { freq: 587, at: 0.08, duration: 0.1 },
  ],
  // Low double: something failed.
  error: [
    { freq: 220, at: 0, duration: 0.09 },
    { freq: 220, at: 0.14, duration: 0.09 },
  ],
  // Two-tone chime: a new approval is waiting (deep link only, D8).
  approval: [
    { freq: 523, at: 0, duration: 0.08 },
    { freq: 784, at: 0.1, duration: 0.12 },
  ],
};

/** Which earcon (if any) a state transition plays. */
export function earconFor(previous: HudState | null, next: HudState): Earcon | null {
  if (previous === null || previous === next) return null;
  if (next === "listening") return "listen";
  if (next === "error") return "error";
  if (next === "awaiting-approval" && previous !== "awaiting-approval") return "approval";
  if ((previous === "speaking" || previous === "thinking") && next === "idle") return "done";
  return null;
}

type AudioContextLike = Pick<
  AudioContext,
  "currentTime" | "destination" | "state" | "createOscillator" | "createGain"
>;

export interface EarconPlayerDeps {
  createContext: () => AudioContextLike | null;
  muted: () => boolean;
  enabled: () => boolean;
}

export class EarconPlayer {
  private context: AudioContextLike | null = null;
  private readonly deps: EarconPlayerDeps;

  constructor(deps: EarconPlayerDeps) {
    this.deps = deps;
  }

  /** Called from a user gesture: creates/resumes the context. */
  unlock(): void {
    if (this.deps.muted() || !this.deps.enabled()) return;
    try {
      this.context ??= this.deps.createContext();
      const ctx = this.context as (AudioContextLike & { resume?: () => Promise<void> }) | null;
      if (ctx && ctx.state === "suspended") void ctx.resume?.().catch(() => undefined);
    } catch {
      // Earcons are optional.
    }
  }

  /** Returns true when the tones were scheduled. */
  play(earcon: Earcon): boolean {
    if (this.deps.muted() || !this.deps.enabled()) return false;
    try {
      this.context ??= this.deps.createContext();
      const ctx = this.context;
      if (!ctx || ctx.state !== "running") return false;
      const start = ctx.currentTime + 0.01;
      for (const tone of EARCONS[earcon]) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = tone.freq;
        const t0 = start + tone.at;
        // Quiet, with a short attack/release so nothing clicks.
        gain.gain.setValueAtTime(0, t0);
        gain.gain.linearRampToValueAtTime(0.08, t0 + 0.01);
        gain.gain.linearRampToValueAtTime(0, t0 + tone.duration);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(t0);
        osc.stop(t0 + tone.duration + 0.02);
      }
      return true;
    } catch {
      return false;
    }
  }
}

export function browserAudioContext(): AudioContextLike | null {
  if (typeof window === "undefined") return null;
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  return Ctor ? new Ctor() : null;
}
