import { describe, expect, it } from "vitest";
import { SILERO_CONTEXT, SILERO_WINDOW, SileroVad } from "@/lib/voice/vad";
import type { SessionLike, TensorLike } from "@/lib/voice/wakeword/tensors";

function fakeSilero(probs: number[]) {
  const calls: Record<string, TensorLike>[] = [];
  let n = 0;
  const session: SessionLike = {
    inputNames: ["input", "state", "sr"],
    outputNames: ["output", "stateN"],
    async run(feeds) {
      calls.push(feeds);
      const p = probs[n] ?? 0;
      n += 1;
      return {
        output: { data: new Float32Array([p]), dims: [1, 1] },
        stateN: { data: new Float32Array(256).fill(n), dims: [2, 1, 128] },
      };
    },
  };
  return { session, calls };
}

describe("SileroVad (v5, mocked ONNX)", () => {
  it("runs 512-sample windows with 64 samples of context, an int64 sample rate and the carried state", async () => {
    const { session, calls } = fakeSilero([0.1, 0.9]);
    const vad = new SileroVad(session);
    const samples = new Float32Array(SILERO_WINDOW * 2).map((_, i) => i / 10_000);
    const prob = await vad.process(samples);
    expect(prob).toBeCloseTo(0.9);
    expect(calls).toHaveLength(2);
    const first = calls[0]!;
    expect(first.input!.dims).toEqual([1, SILERO_CONTEXT + SILERO_WINDOW]);
    expect(first.state!.dims).toEqual([2, 1, 128]);
    expect(first.sr!.type).toBe("int64");
    expect((first.sr!.data as BigInt64Array)[0]).toBe(BigInt(16000));
    // Second window: context = last 64 samples of the first window, state from stateN.
    const second = calls[1]!;
    const secondInput = second.input!.data as Float32Array;
    expect(secondInput[SILERO_CONTEXT - 1]).toBeCloseTo((SILERO_WINDOW - 1) / 10_000);
    expect((second.state!.data as Float32Array)[0]).toBe(1);
  });

  it("buffers partial windows and resets state", async () => {
    const { session, calls } = fakeSilero([0.7, 0.7]);
    const vad = new SileroVad(session);
    expect(await vad.process(new Float32Array(300))).toBe(0);
    expect(calls).toHaveLength(0);
    await vad.process(new Float32Array(300));
    expect(calls).toHaveLength(1);
    vad.reset();
    await vad.process(new Float32Array(SILERO_WINDOW));
    expect((calls[1]!.state!.data as Float32Array).every((v) => v === 0)).toBe(true);
  });
});
