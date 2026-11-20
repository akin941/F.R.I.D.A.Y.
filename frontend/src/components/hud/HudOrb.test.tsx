import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { HudCanvasOrb } from "@/components/hud/HudCanvasOrb";
import { HudOrb } from "@/components/hud/HudOrb";
import type { FrameLoopDeps } from "@/lib/hud/frame-loop";

vi.mock("@/components/hud/orb/HudOrbGL", () => ({
  default: ({ state }: { state: string }) => <div data-testid="hud-orb-gl-stub" data-state={state} />,
}));

function fake2d() {
  const gradient = { addColorStop: vi.fn() };
  return {
    scale: vi.fn(),
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    arc: vi.fn(),
    stroke: vi.fn(),
    fill: vi.fn(),
    createRadialGradient: vi.fn(() => gradient),
    globalAlpha: 1,
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    lineCap: "butt",
  };
}

function fakeRaf() {
  const pending = new Map<number, FrameRequestCallback>();
  let id = 0;
  let now = 0;
  const deps: FrameLoopDeps = {
    raf: (cb) => {
      pending.set(++id, cb);
      return id;
    },
    caf: (handle) => {
      pending.delete(handle);
    },
  };
  return {
    deps,
    pending,
    tick(frames: number) {
      for (let i = 0; i < frames; i += 1) {
        now += 1000 / 60;
        const cbs = [...pending.values()];
        pending.clear();
        cbs.forEach((cb) => cb(now));
      }
    },
  };
}

let ctx: ReturnType<typeof fake2d>;

beforeEach(() => {
  ctx = fake2d();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(((kind: string) =>
    kind === "2d" ? ctx : null) as unknown as HTMLCanvasElement["getContext"]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("HudOrb renderer choice", () => {
  it("falls back to the Canvas-2D orb when WebGL is unavailable", () => {
    render(<HudOrb state="thinking" fps={30} reducedMotion={false} webglAvailable={() => false} />);
    expect(screen.getByTestId("hud-orb")).toHaveAttribute("data-renderer", "canvas");
    expect(screen.getByTestId("hud-orb-canvas")).toHaveAttribute("data-state", "thinking");
    expect(screen.queryByTestId("hud-orb-gl-stub")).toBeNull();
  });

  it("uses the canvas orb under reduced motion even with WebGL", () => {
    render(<HudOrb state="idle" fps={0} reducedMotion webglAvailable={() => true} />);
    expect(screen.getByTestId("hud-orb")).toHaveAttribute("data-renderer", "canvas");
  });

  it("loads the WebGL orb (dynamic import) when WebGL is there", async () => {
    render(<HudOrb state="speaking" fps={30} reducedMotion={false} webglAvailable={() => true} />);
    expect(await screen.findByTestId("hud-orb-gl-stub")).toHaveAttribute("data-state", "speaking");
    expect(screen.getByTestId("hud-orb")).toHaveAttribute("data-renderer", "webgl");
  });

  it("detects WebGL as missing in a context-less browser", () => {
    render(<HudOrb state="idle" fps={5} reducedMotion={false} />);
    expect(screen.getByTestId("hud-orb")).toHaveAttribute("data-renderer", "canvas");
  });
});

describe("HudCanvasOrb frame budget", () => {
  it("reduced motion / 0 fps: one static frame per state, no animation frame loop", () => {
    const raf = fakeRaf();
    const { rerender } = render(<HudCanvasOrb state="idle" fps={0} frameDeps={raf.deps} />);
    expect(raf.pending.size).toBe(0);
    const drawsAfterMount = ctx.clearRect.mock.calls.length;
    expect(drawsAfterMount).toBeGreaterThan(0);
    raf.tick(30);
    expect(ctx.clearRect.mock.calls.length).toBe(drawsAfterMount);
    // A state change draws exactly one more static frame.
    rerender(<HudCanvasOrb state="error" fps={0} frameDeps={raf.deps} />);
    expect(raf.pending.size).toBe(0);
    expect(ctx.clearRect.mock.calls.length).toBe(drawsAfterMount + 1);
  });

  it("animates at the cap, and stops holding frames when the budget drops to 0 (hidden tab)", () => {
    const raf = fakeRaf();
    const { rerender } = render(<HudCanvasOrb state="listening" fps={30} frameDeps={raf.deps} />);
    expect(raf.pending.size).toBe(1);
    const before = ctx.clearRect.mock.calls.length;
    raf.tick(60); // one second at 60 Hz
    const drawn = ctx.clearRect.mock.calls.length - before;
    expect(drawn).toBeGreaterThanOrEqual(29);
    expect(drawn).toBeLessThanOrEqual(31);

    rerender(<HudCanvasOrb state="listening" fps={0} frameDeps={raf.deps} />);
    expect(raf.pending.size).toBe(0);
    const stopped = ctx.clearRect.mock.calls.length;
    raf.tick(60);
    expect(ctx.clearRect.mock.calls.length).toBe(stopped);

    // Visible again: the loop resumes.
    rerender(<HudCanvasOrb state="listening" fps={30} frameDeps={raf.deps} />);
    expect(raf.pending.size).toBe(1);
  });

  it("unmount cancels the pending frame", () => {
    const raf = fakeRaf();
    const { unmount } = render(<HudCanvasOrb state="thinking" fps={30} frameDeps={raf.deps} />);
    expect(raf.pending.size).toBe(1);
    unmount();
    expect(raf.pending.size).toBe(0);
  });
});
