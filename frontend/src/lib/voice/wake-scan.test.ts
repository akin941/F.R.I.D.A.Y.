import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Release-gate scan (Phase 6 branch 4, D6): the modules that see pre-wake
 * audio make no network request of any kind. Only the voice loop
 * (`api.ts`, called by the controller after a detection or a push-to-talk
 * press) uploads audio; only the model loader fetches, and only same-origin
 * model files (tested in model-loader.test.ts).
 */
const DIR = path.resolve(__dirname);
const PRE_WAKE = [
  "wake-engine.ts",
  "wake-mode.ts",
  "wakeword-worker.ts",
  "mic-stream.ts",
  "vad.ts",
  "wakeword/openwakeword.ts",
  "wakeword/tensors.ts",
];
const NETWORK = /\bfetch\(|apiFetch|XMLHttpRequest|sendBeacon|WebSocket|EventSource|apiPostJson|apiPostForm/;

describe("wake mode makes no network request before a detection", () => {
  it.each(PRE_WAKE)("%s has no network call", (file) => {
    const text = readFileSync(path.join(DIR, file), "utf8");
    expect(NETWORK.test(text), file).toBe(false);
  });

  it("the Worker loads the verified bytes it is given, never a URL", () => {
    const worker = readFileSync(path.join(DIR, "wakeword-worker.ts"), "utf8");
    expect(worker).toMatch(/InferenceSession\.create\(new Uint8Array\(bytes\)/);
    expect(worker).toMatch(/numThreads = 1/);
  });
});
