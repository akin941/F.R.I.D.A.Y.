import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { normalizeSpeech } from "@/lib/voice/self-trigger";

// Phase 13 D13: the `home.control` route parses the server-side transcript
// with the Python port of `normalizeSpeech` (`core.speech`); the web voice
// client normalizes the same way. Both are checked against the shared
// vectors (`docs/voice/home-route-vectors.json`, also read by
// backend/connectors/tests/test_home_phrases.py).

interface NormalizeVector {
  input: string;
  speech: string;
}

const vectors = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../docs/voice/home-route-vectors.json"), "utf-8"),
) as { normalize: NormalizeVector[] };

describe("home-route vectors", () => {
  it.each(vectors.normalize.map((row) => [row.input, row.speech]))(
    "normalizeSpeech(%j) matches the shared vector",
    (input, speech) => {
      expect(normalizeSpeech(input)).toBe(speech);
    },
  );
});
