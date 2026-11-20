import { VOICE_SAMPLE_RATE } from "@/lib/voice/config";
import { firstOutput, float32, int64, type SessionLike } from "@/lib/voice/wakeword/tensors";

/**
 * Silero VAD v5 over an injected ONNX session (Phase 6 branch 4, D6), the
 * end-of-utterance detector for wake mode. Mirrors the official
 * `OnnxWrapper` at 16 kHz: 512-sample windows with the previous 64 samples
 * prepended as context, a recurrent `state` [2, 1, 128] carried between
 * windows, `sr` as an int64 scalar. Inputs are float samples in [-1, 1].
 */

export const SILERO_WINDOW = 512;
export const SILERO_CONTEXT = 64;
const STATE_SIZE = 2 * 1 * 128;

export interface VadEngine {
  /** Highest speech probability over the windows completed by these samples. */
  process(samples: Float32Array): Promise<number>;
  reset(): void;
}

export class SileroVad implements VadEngine {
  private readonly session: SessionLike;
  private state = new Float32Array(STATE_SIZE);
  private context = new Float32Array(SILERO_CONTEXT);
  private pending = new Float32Array(0);

  constructor(session: SessionLike) {
    this.session = session;
  }

  reset(): void {
    this.state = new Float32Array(STATE_SIZE);
    this.context = new Float32Array(SILERO_CONTEXT);
    this.pending = new Float32Array(0);
  }

  async process(samples: Float32Array): Promise<number> {
    const merged = new Float32Array(this.pending.length + samples.length);
    merged.set(this.pending, 0);
    merged.set(samples, this.pending.length);
    let best = 0;
    let offset = 0;
    while (merged.length - offset >= SILERO_WINDOW) {
      const window = merged.subarray(offset, offset + SILERO_WINDOW);
      offset += SILERO_WINDOW;
      const p = await this.processWindow(window);
      if (p > best) best = p;
    }
    this.pending = merged.slice(offset);
    return best;
  }

  private async processWindow(window: Float32Array): Promise<number> {
    const input = new Float32Array(SILERO_CONTEXT + SILERO_WINDOW);
    input.set(this.context, 0);
    input.set(window, SILERO_CONTEXT);
    this.context = input.slice(input.length - SILERO_CONTEXT);
    const outputs = await this.session.run({
      input: float32(input, [1, input.length]),
      state: float32(this.state, [2, 1, 128]),
      sr: int64([VOICE_SAMPLE_RATE], []),
    });
    const next = outputs.stateN ?? outputs.state;
    if (next && next.data.length === STATE_SIZE) this.state = Float32Array.from(next.data as ArrayLike<number>);
    const prob = Number(firstOutput(this.session, outputs, "output").data[0] ?? 0);
    return Number.isFinite(prob) ? prob : 0;
  }
}
