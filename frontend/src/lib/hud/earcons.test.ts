import { describe, expect, it, vi } from "vitest";
import { EARCONS, EarconPlayer, earconFor } from "@/lib/hud/earcons";

function fakeContext(state: AudioContextState = "running") {
  const oscillators: { frequency: { value: number }; start: ReturnType<typeof vi.fn> }[] = [];
  const ctx = {
    state,
    currentTime: 0,
    destination: {},
    resume: vi.fn(async () => undefined),
    createOscillator: () => {
      const osc = {
        type: "sine",
        frequency: { value: 0 },
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
      };
      oscillators.push(osc);
      return osc;
    },
    createGain: () => ({
      gain: { setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn() },
      connect: vi.fn(),
    }),
  };
  return { ctx: ctx as unknown as AudioContext, oscillators };
}

describe("earcons (A8: WebAudio oscillators, no assets)", () => {
  it("synthesizes every earcon from tones", () => {
    const { ctx, oscillators } = fakeContext();
    const player = new EarconPlayer({ createContext: () => ctx, muted: () => false, enabled: () => true });
    for (const name of Object.keys(EARCONS) as (keyof typeof EARCONS)[]) {
      expect(player.play(name)).toBe(true);
    }
    const tones = Object.values(EARCONS).flat();
    expect(oscillators.map((o) => o.frequency.value)).toEqual(tones.map((t) => t.freq));
  });

  it("is silent while voice is muted (read at play time)", () => {
    const { ctx, oscillators } = fakeContext();
    let muted = true;
    const create = vi.fn(() => ctx);
    const player = new EarconPlayer({ createContext: create, muted: () => muted, enabled: () => true });
    expect(player.play("listen")).toBe(false);
    expect(create).not.toHaveBeenCalled();
    muted = false;
    expect(player.play("listen")).toBe(true);
    expect(oscillators).toHaveLength(2);
  });

  it("is silent when turned off for the device, or before a gesture", () => {
    const { ctx } = fakeContext("suspended");
    expect(
      new EarconPlayer({ createContext: () => ctx, muted: () => false, enabled: () => false }).play("done"),
    ).toBe(false);
    const player = new EarconPlayer({ createContext: () => ctx, muted: () => false, enabled: () => true });
    expect(player.play("done")).toBe(false);
    player.unlock();
    expect((ctx as unknown as { resume: ReturnType<typeof vi.fn> }).resume).toHaveBeenCalled();
  });

  it("never throws when WebAudio is missing", () => {
    const player = new EarconPlayer({
      createContext: () => {
        throw new Error("no audio");
      },
      muted: () => false,
      enabled: () => true,
    });
    expect(player.play("error")).toBe(false);
  });

  it.each([
    [null, "listening", null],
    ["idle", "listening", "listen"],
    ["listening", "thinking", null],
    ["speaking", "idle", "done"],
    ["idle", "error", "error"],
    ["idle", "awaiting-approval", "approval"],
    ["idle", "idle", null],
  ] as const)("%s -> %s plays %s", (from, to, earcon) => {
    expect(earconFor(from, to)).toBe(earcon);
  });
});
