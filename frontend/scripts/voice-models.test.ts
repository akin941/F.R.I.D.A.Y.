import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { assertPinnedSource, sha256Hex, verifyBytes } from "./voice-models.mjs";
import manifest from "../src/lib/voice/model-manifest.json";

describe("make voice-models helpers", () => {
  const bytes = Buffer.from("model bytes");
  const hash = createHash("sha256").update(bytes).digest("hex");

  it("accepts bytes matching the pinned sha256", () => {
    expect(sha256Hex(bytes)).toBe(hash);
    expect(verifyBytes({ file: "m.onnx", sha256: hash }, bytes)).toBe(true);
  });

  it("rejects a mismatch and an unpinned entry (printing the hash to pin)", () => {
    expect(() => verifyBytes({ file: "m.onnx", sha256: "0".repeat(64) }, bytes)).toThrow(/mismatch/);
    expect(() => verifyBytes({ file: "hey_friday.onnx", sha256: "" }, bytes)).toThrow(new RegExp(hash));
  });

  it("only downloads https release URLs", () => {
    expect(() => assertPinnedSource({ file: "m", source: "http://example.com/m.onnx" })).toThrow(/https/);
    for (const entry of manifest.models) expect(() => assertPinnedSource(entry)).not.toThrow();
  });
});
