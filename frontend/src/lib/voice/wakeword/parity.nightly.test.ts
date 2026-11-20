// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { OWW_CHUNK, OpenWakeWordDetector } from "@/lib/voice/wakeword/openwakeword";
import type { SessionLike, SessionOutput, TensorLike } from "@/lib/voice/wakeword/tensors";

/**
 * Nightly/manual parity test (Phase 6 branch 4 step 0; never in CI: it needs
 * the real models). Compares this TypeScript port, run on real ONNX graphs
 * through onnxruntime-web, against Python openWakeWord's own `AudioFeatures`
 * on the same audio. Prepare the directory with
 * `frontend/scripts/oww-parity.py` (see docs/runbooks/voice-setup.md), then:
 *
 *   VOICE_PARITY_DIR=/path/to/dir npx vitest run src/lib/voice/wakeword/parity.nightly.test.ts
 *
 * The directory holds melspectrogram.onnx, embedding_model.onnx, the keyword
 * model (KEYWORD, default hey_friday.onnx), audio.raw (16 kHz mono PCM16 LE)
 * and oww_out.json (per-chunk embeddings and scores from Python).
 */
const DIR = process.env.VOICE_PARITY_DIR;
const KEYWORD = process.env.VOICE_PARITY_KEYWORD ?? "hey_friday.onnx";

describe.skipIf(!DIR)("openWakeWord parity (real models, nightly/manual)", () => {
  it("matches Python openWakeWord: embeddings and steady-state scores within 0.02", async () => {
    const ort = await import("onnxruntime-web");
    ort.env.wasm.numThreads = 1;
    const load = async (file: string): Promise<SessionLike> => {
      const session = await ort.InferenceSession.create(new Uint8Array(readFileSync(path.join(DIR!, file))));
      return {
        inputNames: session.inputNames,
        outputNames: session.outputNames,
        async run(feeds: Record<string, TensorLike>) {
          const t: Record<string, InstanceType<typeof ort.Tensor>> = {};
          for (const [k, v] of Object.entries(feeds)) {
            t[k] = new ort.Tensor(v.type, v.data as Float32Array, v.dims);
          }
          const out = await session.run(t);
          const r: Record<string, SessionOutput> = {};
          for (const [k, v] of Object.entries(out)) r[k] = { data: v.data as Float32Array, dims: v.dims };
          return r;
        },
      };
    };
    const sessions = {
      melspectrogram: await load("melspectrogram.onnx"),
      embedding: await load("embedding_model.onnx"),
      keyword: await load(KEYWORD),
    };
    // Spy the embedding output per chunk.
    const embeddings: number[][] = [];
    const embedding = sessions.embedding;
    const spying: SessionLike = {
      ...embedding,
      inputNames: embedding.inputNames,
      outputNames: embedding.outputNames,
      async run(feeds) {
        const out = await embedding.run(feeds);
        embeddings.push(Array.from(Object.values(out)[0]!.data as Float32Array));
        return out;
      },
    };
    const detector = new OpenWakeWordDetector({ ...sessions, embedding: spying });
    const raw = readFileSync(path.join(DIR!, "audio.raw"));
    const pcm = new Int16Array(raw.buffer, raw.byteOffset, raw.byteLength / 2);
    const reference = JSON.parse(readFileSync(path.join(DIR!, "oww_out.json"), "utf8")) as {
      embeddings: number[][];
      scores: number[];
    };
    const scores: number[] = [];
    const started = performance.now();
    for (let start = 0; start + OWW_CHUNK <= pcm.length; start += OWW_CHUNK) {
      const chunk = new Float32Array(OWW_CHUNK);
      for (let i = 0; i < OWW_CHUNK; i++) chunk[i] = pcm[start + i]! / 32767;
      scores.push(await detector.process(chunk));
    }
    const coldMs = (performance.now() - started) / scores.length;
    const firstPass = embeddings.length;
    // Warm CPU cost: a second pass (JIT/WASM warmed), the always-on figure.
    const warmStart = performance.now();
    let warmChunks = 0;
    for (let pass = 0; pass < 3; pass++) {
      for (let start = 0; start + OWW_CHUNK <= pcm.length; start += OWW_CHUNK) {
        await detector.process(new Float32Array(OWW_CHUNK));
        warmChunks += 1;
      }
    }
    const msPerChunk = (performance.now() - warmStart) / warmChunks;
    // Warm-up: Python's first chunks lack the 480-sample overlap and start
    // from the all-ones mel prefill, so the first ~0.8 s differ by design;
    // from chunk WARMUP on both pipelines see identical inputs.
    const WARMUP = 10;
    let embDelta = 0;
    reference.embeddings.forEach((ref, i) => {
      if (i < WARMUP) return;
      ref.forEach((v, j) => {
        embDelta = Math.max(embDelta, Math.abs(v - (embeddings[i]?.[j] ?? Infinity)));
      });
    });
    // Steady state: from the 16th chunk on, both score the same 16 real embeddings.
    let scoreDelta = 0;
    for (let i = WARMUP + 15; i < scores.length; i++) {
      scoreDelta = Math.max(scoreDelta, Math.abs(scores[i]! - reference.scores[i]!));
    }
    console.log(`parity: max embedding delta ${embDelta.toExponential(2)}, max score delta ${scoreDelta.toExponential(2)}, cold ${coldMs.toFixed(2)} ms, warm ${msPerChunk.toFixed(2)} ms per 80 ms chunk (${((msPerChunk / 80) * 100).toFixed(1)}% of one core)`);
    expect(firstPass).toBe(reference.embeddings.length);
    expect(embDelta).toBeLessThanOrEqual(0.02);
    expect(scoreDelta).toBeLessThanOrEqual(0.02);
  }, 120_000);
});
