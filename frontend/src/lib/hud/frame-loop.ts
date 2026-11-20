/**
 * The HUD's frame budget (Phase 6 brief A8): 30 fps while a state is
 * active, 5 fps at rest, 0 while the tab is hidden, 0 under reduced motion
 * (one static frame per change instead), and -- on battery -- no always-on
 * resting animation unless the owner turned it on for this device.
 *
 * `FrameLoop` wraps `requestAnimationFrame`: it only calls `draw` once the
 * cap's interval has elapsed, and it holds NO pending rAF at all when the
 * budget is 0 (hidden tab, reduced motion, battery rest) -- the browser then
 * does no per-frame work for the HUD.
 */

export const FPS_ACTIVE = 30;
export const FPS_IDLE = 5;

export interface FrameBudgetInput {
  active: boolean;
  reducedMotion: boolean;
  hidden: boolean;
  onBattery: boolean;
  animateOnBattery: boolean;
}

export function frameBudget(input: FrameBudgetInput): number {
  if (input.hidden || input.reducedMotion) return 0;
  if (input.active) return FPS_ACTIVE;
  if (input.onBattery && !input.animateOnBattery) return 0;
  return FPS_IDLE;
}

export interface FrameLoopDeps {
  raf: (cb: FrameRequestCallback) => number;
  caf: (id: number) => void;
}

export function browserFrameDeps(): FrameLoopDeps {
  return {
    raf: (cb) => window.requestAnimationFrame(cb),
    caf: (id) => window.cancelAnimationFrame(id),
  };
}

export class FrameLoop {
  private frameId: number | null = null;
  private last = -Infinity;
  private readonly draw: (now: number) => void;
  private readonly fps: () => number;
  private readonly deps: FrameLoopDeps;
  /** Frames actually drawn (tests, the manual Mac pass). */
  drawn = 0;

  constructor(draw: (now: number) => void, fps: () => number, deps: FrameLoopDeps = browserFrameDeps()) {
    this.draw = draw;
    this.fps = fps;
    this.deps = deps;
  }

  get running(): boolean {
    return this.frameId !== null;
  }

  /** Re-evaluate the budget (state, visibility, motion or battery changed). */
  refresh(): void {
    if (this.fps() <= 0) {
      this.stop();
      return;
    }
    if (this.frameId === null) {
      this.last = -Infinity;
      this.frameId = this.deps.raf(this.tick);
    }
  }

  stop(): void {
    if (this.frameId !== null) this.deps.caf(this.frameId);
    this.frameId = null;
  }

  private tick = (now: number) => {
    this.frameId = null;
    const fps = this.fps();
    if (fps <= 0) return;
    // Half a millisecond of slack so a 60 Hz display holds an exact 30 fps.
    if (now - this.last >= 1000 / fps - 0.5) {
      this.last = now;
      this.drawn += 1;
      this.draw(now);
    }
    this.frameId = this.deps.raf(this.tick);
  };
}

/** `prefers-reduced-motion`, live. */
export function watchReducedMotion(onChange: (reduced: boolean) => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => undefined;
  const query = window.matchMedia("(prefers-reduced-motion: reduce)");
  onChange(query.matches);
  const listener = (event: MediaQueryListEvent) => onChange(event.matches);
  query.addEventListener?.("change", listener);
  return () => query.removeEventListener?.("change", listener);
}

/** `document.hidden`, live. */
export function watchHidden(onChange: (hidden: boolean) => void): () => void {
  if (typeof document === "undefined") return () => undefined;
  const listener = () => onChange(document.visibilityState === "hidden");
  listener();
  document.addEventListener("visibilitychange", listener);
  return () => document.removeEventListener("visibilitychange", listener);
}

interface BatteryLike extends EventTarget {
  charging: boolean;
}

/**
 * On battery (Battery Status API, Chromium only). Unknown -- Safari,
 * Firefox, no API -- reads as NOT on battery, so the resting animation
 * stays at its 5 fps cap; the per-device toggle can still switch the
 * resting animation off entirely.
 */
export function watchBattery(
  onChange: (onBattery: boolean) => void,
  nav: Navigator | undefined = typeof navigator === "undefined" ? undefined : navigator,
): () => void {
  const getBattery = (nav as (Navigator & { getBattery?: () => Promise<BatteryLike> }) | undefined)
    ?.getBattery;
  if (!nav || typeof getBattery !== "function") {
    onChange(false);
    return () => undefined;
  }
  let battery: BatteryLike | null = null;
  let cancelled = false;
  const listener = () => {
    if (battery) onChange(!battery.charging);
  };
  getBattery
    .call(nav)
    .then((value) => {
      if (cancelled) return;
      battery = value;
      listener();
      value.addEventListener("chargingchange", listener);
    })
    .catch(() => onChange(false));
  return () => {
    cancelled = true;
    battery?.removeEventListener("chargingchange", listener);
  };
}
