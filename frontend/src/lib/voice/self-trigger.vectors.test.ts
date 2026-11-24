/**
 * Phase 11 brief D10: the shared self-trigger vectors. The node's voice
 * child runs a Python port of `self-trigger.ts`
 * (`node/friday-voice/friday_voice/self_trigger.py`); both implementations
 * must give exactly these outputs, so the browser and the Mac agree on what
 * counts as Friday hearing itself. pytest runs the same file
 * (`node/friday-voice/tests/test_self_trigger.py`).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { matchesRecentSpeech, normalizeSpeech, withinSelfTriggerWindow } from "@/lib/voice/self-trigger";

interface MatchCase {
  id: string;
  transcript: string;
  recent: string[];
  wake_phrase: string | null;
  expected: boolean;
}

interface WindowCase {
  id: string;
  detected_at: number;
  playback_ended_at: number | null;
  window_ms: number | null;
  expected: boolean;
}

interface Vectors {
  normalize: { input: string; expected: string }[];
  match: MatchCase[];
  window: WindowCase[];
}

const VECTORS_PATH = resolve(__dirname, "../../../../node/friday-voice/vectors/self_trigger.json");
const vectors = JSON.parse(readFileSync(VECTORS_PATH, "utf-8")) as Vectors;

describe("shared self-trigger vectors", () => {
  it("has cases", () => {
    expect(vectors.normalize.length).toBeGreaterThan(10);
    expect(vectors.match.length).toBeGreaterThan(10);
    expect(vectors.window.length).toBeGreaterThan(5);
  });

  it.each(vectors.normalize.map((v) => [v.input, v.expected]))("normalizeSpeech(%j)", (input, expected) => {
    expect(normalizeSpeech(input)).toBe(expected);
  });

  it.each(vectors.match.map((v) => [v.id, v]))("match %s", (_id, vector) => {
    const recent = vector.recent.map((text) => ({ text, at: 0 }));
    const result =
      vector.wake_phrase === null
        ? matchesRecentSpeech(vector.transcript, recent)
        : matchesRecentSpeech(vector.transcript, recent, vector.wake_phrase);
    expect(result).toBe(vector.expected);
  });

  it.each(vectors.window.map((v) => [v.id, v]))("window %s", (_id, vector) => {
    const result =
      vector.window_ms === null
        ? withinSelfTriggerWindow(vector.detected_at, vector.playback_ended_at)
        : withinSelfTriggerWindow(vector.detected_at, vector.playback_ended_at, vector.window_ms);
    expect(result).toBe(vector.expected);
  });
});
