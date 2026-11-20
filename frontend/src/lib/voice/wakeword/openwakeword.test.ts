import { describe, expect, it } from "vitest";
import {
  OWW_CHUNK,
  OWW_EMBEDDING_DIM,
  OWW_EMBEDDING_WINDOW,
  OWW_KEYWORD_WINDOW,
  OWW_MEL_BINS,
  OWW_OVERLAP,
  OpenWakeWordDetector,
} from "@/lib/voice/wakeword/openwakeword";
import type { SessionLike, SessionOutput, TensorLike } from "@/lib/voice/wakeword/tensors";

interface FakeSession extends SessionLike {
  calls: Record<string, TensorLike>[];
}

function fakeSession(
  inputName: string,
  outputName: string,
  respond: (feeds: Record<string, TensorLike>) => SessionOutput,
): FakeSession {
  const calls: Record<string, TensorLike>[] = [];
  return {
    inputNames: [inputName],
    outputNames: [outputName],
    calls,
    async run(feeds) {
      calls.push(feeds);
      return { [outputName]: respond(feeds) };
    },
  };
}

function setup(score = 0.9) {
  const mel = fakeSession("input", "output", () => ({
    // 8 mel frames per 80 ms chunk, raw value 10 -> transformed 10/10+2 = 3
    data: new Float32Array(8 * OWW_MEL_BINS).fill(10),
    dims: [1, 1, 8, OWW_MEL_BINS],
  }));
  const embedding = fakeSession("input_1", "conv2d_19", () => ({
    data: new Float32Array(OWW_EMBEDDING_DIM).fill(0.5),
    dims: [1, 1, 1, OWW_EMBEDDING_DIM],
  }));
  const keyword = fakeSession("x", "y", () => ({ data: new Float32Array([score]), dims: [1, 1] }));
  const detector = new OpenWakeWordDetector({ melspectrogram: mel, embedding, keyword });
  return { detector, mel, embedding, keyword };
}

describe("OpenWakeWordDetector (openWakeWord streaming pipeline, mocked ONNX)", () => {
  it("runs the melspectrogram on 1280-sample chunks plus the 480-sample overlap, int16 scale", async () => {
    const { detector, mel } = setup();
    await detector.process(new Float32Array(OWW_CHUNK).fill(0.5));
    expect(mel.calls).toHaveLength(1);
    const input = mel.calls[0]!.input!;
    expect(input.dims).toEqual([1, OWW_OVERLAP + OWW_CHUNK]);
    const data = input.data as Float32Array;
    expect(data[0]).toBe(0); // the (empty) overlap
    expect(data[OWW_OVERLAP]).toBeCloseTo(0.5 * 32767, 1);
  });

  it("buffers partial chunks and never holds more than 2 s of raw audio", async () => {
    const { detector, mel } = setup();
    await detector.process(new Float32Array(1000));
    expect(mel.calls).toHaveLength(0);
    await detector.process(new Float32Array(300));
    expect(mel.calls).toHaveLength(1);
    for (let i = 0; i < 200; i++) {
      await detector.process(new Float32Array(1600));
      expect(detector.heldSamples()).toBeLessThanOrEqual(2 * 16_000);
    }
  });

  it("feeds 76 transformed mel frames to the embedding graph and 16 embeddings to the keyword head", async () => {
    const { detector, embedding, keyword } = setup(0.8);
    const scores: number[] = [];
    for (let i = 0; i < OWW_KEYWORD_WINDOW; i++) scores.push(await detector.process(new Float32Array(OWW_CHUNK)));
    // Scores are 0 until 16 real embeddings exist, then the head's output.
    expect(scores.slice(0, OWW_KEYWORD_WINDOW - 1).every((s) => s === 0)).toBe(true);
    expect(scores[OWW_KEYWORD_WINDOW - 1]).toBeCloseTo(0.8);
    const embInput = embedding.calls.at(-1)!.input_1!;
    expect(embInput.dims).toEqual([1, OWW_EMBEDDING_WINDOW, OWW_MEL_BINS, 1]);
    expect((embInput.data as Float32Array)[embInput.data.length - 1]).toBeCloseTo(3); // x/10 + 2
    const kwInput = keyword.calls.at(-1)!.x!;
    expect(kwInput.dims).toEqual([1, OWW_KEYWORD_WINDOW, OWW_EMBEDDING_DIM]);
    expect(keyword.calls).toHaveLength(1);
  });

  it("reset() forgets every buffered feature (no re-fire on stale features)", async () => {
    const { detector, keyword } = setup(0.9);
    for (let i = 0; i < OWW_KEYWORD_WINDOW; i++) await detector.process(new Float32Array(OWW_CHUNK));
    expect(keyword.calls).toHaveLength(1);
    detector.reset();
    expect(await detector.process(new Float32Array(OWW_CHUNK))).toBe(0);
    expect(detector.heldSamples()).toBe(OWW_OVERLAP);
  });
});
