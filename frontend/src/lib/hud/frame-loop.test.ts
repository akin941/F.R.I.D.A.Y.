import { afterEach, describe, expect, it, vi } from "vitest";
import {
  FPS_ACTIVE,
  FPS_IDLE,
  FrameLoop,
  frameBudget,
  watchBattery,
  watchHidden,
  type FrameLoopDeps,
} from "@/lib/hud/frame-loop";
import { setDocumentHidden } from "@/test/visibility";

/** A fake rAF at a given display rate, advanced by hand. */
function fakeRaf(hz = 60) {
  let now = 0;
  let nextId = 1;
  const pending = new Map<number, FrameRequestCallback>();
  const deps: FrameLoopDeps = {
    raf: (cb) => {
      const id = nextId++;
      pending.set(id, cb);
      return id;
    },
    caf: (id) => {
      pending.delete(id);
    },
  };
  return {
    deps,
    pending,
    /** Run `seconds` of display frames. */
    run(seconds: number) {
      const frames = Math.round(seconds * hz);
      for (let i = 0; i < frames; i += 1) {
        now += 1000 / hz;
        const callbacks = [...pending.values()];
        pending.clear();
        for (const cb of callbacks) cb(now);
      }
    },
  };
}

afterEach(() => {
  setDocumentHidden(false);
});

describe("frameBudget (A8)", () => {
  const base = { active: false, reducedMotion: false, hidden: false, onBattery: false, animateOnBattery: false };

  it.each([
    [{ active: true }, FPS_ACTIVE],
    [{}, FPS_IDLE],
    [{ hidden: true, active: true }, 0],
    [{ reducedMotion: true, active: true }, 0],
    // On battery: no always-on resting animation unless turned on...
    [{ onBattery: true }, 0],
    [{ onBattery: true, animateOnBattery: true }, FPS_IDLE],
    // ...a live voice state still animates.
    [{ onBattery: true, active: true }, FPS_ACTIVE],
  ])("%j -> %i fps", (input, fps) => {
    expect(frameBudget({ ...base, ...input })).toBe(fps);
  });

  it("caps are 30 active / 5 idle", () => {
    expect(FPS_ACTIVE).toBe(30);
    expect(FPS_IDLE).toBe(5);
  });
});

describe("FrameLoop", () => {
  it("respects the 30 fps cap on a 60 Hz display", () => {
    const raf = fakeRaf(60);
    const draw = vi.fn();
    const loop = new FrameLoop(draw, () => 30, raf.deps);
    loop.refresh();
    raf.run(2);
    expect(draw.mock.calls.length).toBeGreaterThanOrEqual(59);
    expect(draw.mock.calls.length).toBeLessThanOrEqual(61);
  });

  it("respects the 5 fps idle cap on a 120 Hz display", () => {
    const raf = fakeRaf(120);
    const draw = vi.fn();
    const loop = new FrameLoop(draw, () => 5, raf.deps);
    loop.refresh();
    raf.run(4);
    expect(draw.mock.calls.length).toBeGreaterThanOrEqual(19);
    expect(draw.mock.calls.length).toBeLessThanOrEqual(21);
  });

  it("holds no pending frame at 0 fps and resumes on refresh", () => {
    const raf = fakeRaf(60);
    let fps = 0;
    const draw = vi.fn();
    const loop = new FrameLoop(draw, () => fps, raf.deps);
    loop.refresh();
    expect(loop.running).toBe(false);
    expect(raf.pending.size).toBe(0);
    raf.run(1);
    expect(draw).not.toHaveBeenCalled();

    fps = 30;
    loop.refresh();
    raf.run(1);
    expect(draw).toHaveBeenCalled();

    // The budget dropping to 0 mid-run ends the loop at the next frame.
    fps = 0;
    raf.run(0.1);
    expect(loop.running).toBe(false);
    expect(raf.pending.size).toBe(0);
  });

  it("stop() cancels the pending frame", () => {
    const raf = fakeRaf(60);
    const loop = new FrameLoop(vi.fn(), () => 30, raf.deps);
    loop.refresh();
    expect(raf.pending.size).toBe(1);
    loop.stop();
    expect(raf.pending.size).toBe(0);
  });
});

describe("environment watchers", () => {
  it("reports the hidden tab live", () => {
    const seen: boolean[] = [];
    const stop = watchHidden((hidden) => seen.push(hidden));
    setDocumentHidden(true);
    setDocumentHidden(false);
    stop();
    setDocumentHidden(true);
    expect(seen).toEqual([false, true, false]);
  });

  it("reads battery from the Battery Status API", async () => {
    const battery = Object.assign(new EventTarget(), { charging: false });
    const nav = { getBattery: () => Promise.resolve(battery) } as unknown as Navigator;
    const seen: boolean[] = [];
    const stop = watchBattery((onBattery) => seen.push(onBattery), nav);
    await Promise.resolve();
    await Promise.resolve();
    battery.charging = true;
    battery.dispatchEvent(new Event("chargingchange"));
    stop();
    expect(seen).toEqual([true, false]);
  });

  it("treats a missing Battery API as not on battery", () => {
    const seen: boolean[] = [];
    watchBattery((onBattery) => seen.push(onBattery), {} as Navigator);
    expect(seen).toEqual([false]);
  });
});
