import manifest from "@/lib/voice/model-manifest.json";

/**
 * The wake-word model manifest (Phase 6 branch 4, D6), typed.
 *
 * The JSON next to this file is the single source of truth: `make
 * voice-models` (frontend/scripts/voice-models.mjs) reads it to download and
 * verify the files, and the browser reads it to refuse arming on any hash
 * mismatch. Every model URL is same-origin (`/voice-models/...`) -- no CDN.
 * The wake phrase is configurable here: swapping or retraining the keyword
 * model ("hey_friday", owner-trained) is a manifest change, not a code change.
 */

export type ModelRole = "melspectrogram" | "embedding" | "keyword" | "vad";

export interface ModelEntry {
  role: ModelRole;
  file: string;
  /** Pinned upstream release URL used only by `make voice-models` (null: owner-provided). */
  source: string | null;
  /** Lower-case hex sha256; empty = not pinned yet (arming is refused). */
  sha256: string;
  license: string;
}

export interface ModelManifest {
  basePath: string;
  wakePhrase: string;
  models: ModelEntry[];
  runtime: { package: string; dir: string; files: string[] };
}

export const MODEL_MANIFEST: ModelManifest = manifest as ModelManifest;

export const MODEL_ROLES: readonly ModelRole[] = ["melspectrogram", "embedding", "keyword", "vad"];

/** The same-origin URL (path) a model is served from. */
export function modelUrl(entry: ModelEntry, m: ModelManifest = MODEL_MANIFEST): string {
  return `${m.basePath}${entry.file}`;
}

/** Where the onnxruntime-web WASM runtime is served from (same origin). */
export function runtimeBasePath(m: ModelManifest = MODEL_MANIFEST): string {
  return `${m.basePath}${m.runtime.dir}`;
}

export function wakePhrase(m: ModelManifest = MODEL_MANIFEST): string {
  return m.wakePhrase;
}
