import { VOICE_SAMPLE_RATE, VOICE_WAKE_RING_SECONDS } from "@/lib/voice/config";
import { firstOutput, float32, type SessionLike } from "@/lib/voice/wakeword/tensors";

/**
 * The openWakeWord streaming pipeline, ported to TypeScript (Phase 6
 * branch 4, D6). Mirrors `openwakeword/utils.py` (`AudioFeatures`, v0.6):
 *
 *   80 ms chunks (1280 samples, int16 scale) + the previous 480 samples
 *     -> melspectrogram graph -> x/10 + 2 -> mel buffer (32 bins/frame)
 *   last 76 mel frames -> embedding graph -> one 96-dim embedding per chunk
 *   last 16 embeddings -> keyword head (`hey_friday`) -> score 0..1
 *
 * Pre-wake audio is held only here, inside the Worker: at most one chunk of
 * raw samples plus the 480-sample overlap (well under the 2 s ring bound),
 * the mel frames (~1 s) and the embeddings (~1.3 s), all overwritten
 * continuously. Nothing here is ever posted out of the Worker except a score.
 *
 * Parity note: Python pre-fills the embedding buffer with embeddings of
 * random noise; this port instead returns 0 until 16 real embeddings exist
 * (~1.3 s after arming). The nightly/manual parity test against Python
 * openWakeWord (owner step, score delta <= 0.02) compares steady-state scores.
 */

export const OWW_CHUNK = 1280;
export const OWW_OVERLAP = 160 * 3;
export const OWW_MEL_BINS = 32;
export const OWW_EMBEDDING_WINDOW = 76;
export const OWW_EMBEDDING_DIM = 96;
export const OWW_KEYWORD_WINDOW = 16;
const MEL_MAX_FRAMES = 100;

export interface WakeDetector {
  /** Feed 16 kHz mono float samples in [-1, 1]; resolves to the highest score of the chunks completed. */
  process(samples: Float32Array): Promise<number>;
  /** Forget every buffered sample and feature (after an utterance, on pause). */
  reset(): void;
  /** Raw pre-wake samples currently held (for the D6 ring-bound test). */
  heldSamples(): number;
}

export interface OpenWakeWordSessions {
  melspectrogram: SessionLike;
  embedding: SessionLike;
  keyword: SessionLike;
}

export class OpenWakeWordDetector implements WakeDetector {
  private readonly sessions: OpenWakeWordSessions;
  private pending = new Float32Array(0);
  private tail = new Float32Array(OWW_OVERLAP);
  private mel: Float32Array[] = [];
  private features: Float32Array[] = [];

  constructor(sessions: OpenWakeWordSessions) {
    this.sessions = sessions;
    this.reset();
  }

  reset(): void {
    this.pending = new Float32Array(0);
    this.tail = new Float32Array(OWW_OVERLAP);
    // openWakeWord starts the mel buffer with 76 frames of ones.
    this.mel = Array.from({ length: OWW_EMBEDDING_WINDOW }, () => new Float32Array(OWW_MEL_BINS).fill(1));
    this.features = [];
  }

  heldSamples(): number {
    return this.pending.length + this.tail.length;
  }

  async process(samples: Float32Array): Promise<number> {
    const merged = new Float32Array(this.pending.length + samples.length);
    merged.set(this.pending, 0);
    for (let i = 0; i < samples.length; i++) {
      // openWakeWord feeds int16-scale values to the melspectrogram graph.
      merged[this.pending.length + i] = Math.max(-1, Math.min(1, samples[i]!)) * 32767;
    }
    let best = 0;
    let offset = 0;
    while (merged.length - offset >= OWW_CHUNK) {
      const chunk = merged.subarray(offset, offset + OWW_CHUNK);
      offset += OWW_CHUNK;
      const score = await this.processChunk(chunk);
      if (score > best) best = score;
    }
    this.pending = merged.slice(offset);
    if (this.heldSamples() > VOICE_WAKE_RING_SECONDS * VOICE_SAMPLE_RATE) {
      // Unreachable by construction; a hard bound all the same (D6).
      this.pending = this.pending.slice(-OWW_CHUNK);
    }
    return best;
  }

  private async processChunk(chunk: Float32Array): Promise<number> {
    const input = new Float32Array(OWW_OVERLAP + OWW_CHUNK);
    input.set(this.tail, 0);
    input.set(chunk, OWW_OVERLAP);
    this.tail = chunk.slice(OWW_CHUNK - OWW_OVERLAP);

    const melSession = this.sessions.melspectrogram;
    const melOut = firstOutput(
      melSession,
      await melSession.run({ [melSession.inputNames[0] ?? "input"]: float32(input, [1, input.length]) }),
    );
    const values = melOut.data;
    const frames = Math.floor(values.length / OWW_MEL_BINS);
    for (let f = 0; f < frames; f++) {
      const frame = new Float32Array(OWW_MEL_BINS);
      for (let b = 0; b < OWW_MEL_BINS; b++) frame[b] = Number(values[f * OWW_MEL_BINS + b]) / 10 + 2;
      this.mel.push(frame);
    }
    if (this.mel.length > MEL_MAX_FRAMES) this.mel.splice(0, this.mel.length - MEL_MAX_FRAMES);
    if (this.mel.length < OWW_EMBEDDING_WINDOW) return 0;

    const window = new Float32Array(OWW_EMBEDDING_WINDOW * OWW_MEL_BINS);
    const start = this.mel.length - OWW_EMBEDDING_WINDOW;
    for (let f = 0; f < OWW_EMBEDDING_WINDOW; f++) window.set(this.mel[start + f]!, f * OWW_MEL_BINS);
    const embSession = this.sessions.embedding;
    const embOut = firstOutput(
      embSession,
      await embSession.run({
        [embSession.inputNames[0] ?? "input_1"]: float32(window, [1, OWW_EMBEDDING_WINDOW, OWW_MEL_BINS, 1]),
      }),
    );
    const embedding = new Float32Array(OWW_EMBEDDING_DIM);
    for (let i = 0; i < OWW_EMBEDDING_DIM; i++) embedding[i] = Number(embOut.data[i] ?? 0);
    this.features.push(embedding);
    if (this.features.length > OWW_KEYWORD_WINDOW) this.features.shift();
    if (this.features.length < OWW_KEYWORD_WINDOW) return 0;

    const stacked = new Float32Array(OWW_KEYWORD_WINDOW * OWW_EMBEDDING_DIM);
    this.features.forEach((f, i) => stacked.set(f, i * OWW_EMBEDDING_DIM));
    const kwSession = this.sessions.keyword;
    const kwOut = firstOutput(
      kwSession,
      await kwSession.run({
        [kwSession.inputNames[0] ?? "input"]: float32(stacked, [1, OWW_KEYWORD_WINDOW, OWW_EMBEDDING_DIM]),
      }),
    );
    const score = Number(kwOut.data[0] ?? 0);
    return Number.isFinite(score) ? score : 0;
  }
}
