import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { normalizeSpeech } from "@/lib/voice/self-trigger";

// Phase 12 D8: the wake-route normalizer is a Python port of
// `normalizeSpeech`; both are checked against the same shared vectors
// (`docs/voice/wake-route-vectors.json`, also read by
// backend/assistant/tests/test_wake_routes.py).

interface NormalizeVector {
  input: string;
  speech: string;
  utterance: string;
}

const vectors = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../docs/voice/wake-route-vectors.json"), "utf-8"),
) as { normalize: NormalizeVector[] };

describe("wake-route vectors", () => {
  it.each(vectors.normalize.map((row) => [row.input, row.speech]))(
    "normalizeSpeech(%j) matches the shared vector",
    (input, speech) => {
      expect(normalizeSpeech(input)).toBe(speech);
    },
  );
});
