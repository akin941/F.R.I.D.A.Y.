import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import {
  ModelIntegrityError,
  isSameOrigin,
  loadVerifiedModels,
  toHex,
} from "@/lib/voice/model-loader";
import {
  MODEL_MANIFEST,
  MODEL_ROLES,
  modelUrl,
  runtimeBasePath,
  type ModelManifest,
} from "@/lib/voice/model-manifest";

const ORIGIN = "http://localhost:3001";

function bytesFor(name: string): ArrayBuffer {
  const data = new TextEncoder().encode(`model:${name}`);
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
}

function sha(buffer: ArrayBuffer): string {
  return createHash("sha256").update(new Uint8Array(buffer)).digest("hex");
}

const nodeDigest = async (data: ArrayBuffer) => {
  const hash = createHash("sha256").update(new Uint8Array(data)).digest();
  return hash.buffer.slice(hash.byteOffset, hash.byteOffset + hash.byteLength) as ArrayBuffer;
};

/** A manifest with every model pinned to the fake bytes served below. */
function pinnedManifest(overrides: Partial<Record<string, Partial<ModelManifest["models"][number]>>> = {}): ModelManifest {
  return {
    ...MODEL_MANIFEST,
    models: MODEL_MANIFEST.models.map((m) => ({
      ...m,
      sha256: sha(bytesFor(m.file)),
      ...(overrides[m.role] ?? {}),
    })),
  };
}

function servingFetch(served: Record<string, ArrayBuffer | null> = {}) {
  return vi.fn(async (url: string) => {
    const file = url.split("/").pop()!;
    const body = file in served ? served[file] : bytesFor(file);
    if (!body) return new Response("missing", { status: 404 });
    return new Response(body, { status: 200 });
  });
}

describe("model manifest", () => {
  it("lists the four D6 models, every one same-origin under /voice-models/ (no CDN)", () => {
    expect(MODEL_MANIFEST.models.map((m) => m.role).sort()).toEqual([...MODEL_ROLES].sort());
    for (const entry of MODEL_MANIFEST.models) {
      const url = modelUrl(entry);
      expect(url.startsWith("/voice-models/")).toBe(true);
      expect(isSameOrigin(url, ORIGIN)).toBe(true);
      expect(new URL(url, ORIGIN).origin).toBe(ORIGIN);
    }
    expect(isSameOrigin(runtimeBasePath(), ORIGIN)).toBe(true);
  });

  it("pins the downloadable models by sha256 and https release URLs", () => {
    for (const entry of MODEL_MANIFEST.models) {
      if (entry.source) {
        expect(entry.sha256).toMatch(/^[0-9a-f]{64}$/);
        expect(new URL(entry.source).protocol).toBe("https:");
      }
    }
  });

  it("keeps the wake phrase and keyword model configurable (Hey Friday, owner-trained)", () => {
    expect(MODEL_MANIFEST.wakePhrase).toBe("Hey Friday");
    const keyword = MODEL_MANIFEST.models.find((m) => m.role === "keyword")!;
    expect(keyword.file).toBe("hey_friday.onnx");
    expect(keyword.source).toBeNull();
  });
});

describe("loadVerifiedModels", () => {
  it("returns the verified bytes for every role", async () => {
    const fetchImpl = servingFetch();
    const models = await loadVerifiedModels({ manifest: pinnedManifest(), fetchImpl, digest: nodeDigest, origin: ORIGIN });
    expect(Object.keys(models).sort()).toEqual([...MODEL_ROLES].sort());
    expect(fetchImpl).toHaveBeenCalledTimes(4);
    for (const [url] of fetchImpl.mock.calls) expect(isSameOrigin(url, ORIGIN)).toBe(true);
  });

  it("refuses on a sha256 mismatch (tampered or stale file)", async () => {
    const fetchImpl = servingFetch({ "embedding_model.onnx": bytesFor("something else") });
    await expect(
      loadVerifiedModels({ manifest: pinnedManifest(), fetchImpl, digest: nodeDigest, origin: ORIGIN }),
    ).rejects.toMatchObject({ code: "model_hash_mismatch", file: "embedding_model.onnx" });
  });

  it("refuses a missing file", async () => {
    const fetchImpl = servingFetch({ "silero_vad.onnx": null });
    await expect(
      loadVerifiedModels({ manifest: pinnedManifest(), fetchImpl, digest: nodeDigest, origin: ORIGIN }),
    ).rejects.toMatchObject({ code: "model_missing" });
  });

  it("refuses an unpinned model before any request (the shipped hey_friday entry)", async () => {
    const fetchImpl = servingFetch();
    await expect(
      loadVerifiedModels({ manifest: MODEL_MANIFEST, fetchImpl, digest: nodeDigest, origin: ORIGIN }),
    ).rejects.toMatchObject({ code: "model_unpinned", file: "hey_friday.onnx" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a cross-origin model URL before any request", async () => {
    const manifest = { ...pinnedManifest(), basePath: "https://cdn.example.com/models/" };
    const fetchImpl = servingFetch();
    const err = await loadVerifiedModels({ manifest, fetchImpl, digest: nodeDigest, origin: ORIGIN }).catch((e) => e);
    expect(err).toBeInstanceOf(ModelIntegrityError);
    expect(err.code).toBe("model_cross_origin");
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(isSameOrigin("//cdn.example.com/x.onnx", ORIGIN)).toBe(false);
  });

  it("hex-encodes digests", () => {
    expect(toHex(new Uint8Array([0, 15, 255]).buffer)).toBe("000fff");
  });
});
