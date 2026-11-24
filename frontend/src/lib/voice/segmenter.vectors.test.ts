/**
 * Phase 11 brief D8: the shared segmenter vectors. The node's voice child
 * runs a Python port of `segmenter.ts` (`node/friday-voice/friday_voice/segmenter.py`);
 * both implementations must give exactly these outputs, so the browser and
 * the Mac speak a reply in the same sentences. pytest runs the same file
 * (`node/friday-voice/tests/test_segmenter.py`).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { SentenceSegmenter, speakableLine } from "@/lib/voice/segmenter";

interface SegmentCase {
  id: string;
  chunks: string[];
  live: string[][];
  flush: string[];
}

interface Vectors {
  speakable_line: { input: string; expected: string }[];
  segment: SegmentCase[];
}

const VECTORS_PATH = resolve(__dirname, "../../../../node/friday-voice/vectors/segmenter.json");
const vectors = JSON.parse(readFileSync(VECTORS_PATH, "utf-8")) as Vectors;

describe("shared segmenter vectors", () => {
  it("has cases", () => {
    expect(vectors.speakable_line.length).toBeGreaterThan(5);
    expect(vectors.segment.length).toBeGreaterThan(10);
  });

  it.each(vectors.speakable_line.map((v) => [v.input, v.expected]))("speakableLine(%j)", (input, expected) => {
    expect(speakableLine(input)).toBe(expected);
  });

  it.each(vectors.segment.map((v) => [v.id, v]))("segment %s", (_id, vector) => {
    const segmenter = new SentenceSegmenter();
    const live = vector.chunks.map((chunk) => segmenter.push(chunk));
    expect(live).toEqual(vector.live);
    expect(segmenter.flush()).toEqual(vector.flush);
  });
});
