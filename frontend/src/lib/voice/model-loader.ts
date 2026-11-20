import {
  MODEL_MANIFEST,
  MODEL_ROLES,
  modelUrl,
  type ModelManifest,
  type ModelRole,
} from "@/lib/voice/model-manifest";

/**
 * Loads and verifies the wake-word models before wake mode may arm (D6).
 *
 * Release-gate rules made mechanical: a model URL that is not same-origin is
 * refused before any request; a missing file, an unpinned entry (empty
 * sha256) or a sha256 mismatch refuses to arm. The verified bytes are what
 * the Worker loads (they are transferred to it), so the checked file is the
 * file that runs -- the Worker never fetches a model itself.
 */

export type ModelErrorCode =
  | "model_cross_origin"
  | "model_unpinned"
  | "model_missing"
  | "model_hash_mismatch"
  | "model_unsupported";

export class ModelIntegrityError extends Error {
  code: ModelErrorCode;
  file: string;
  constructor(code: ModelErrorCode, file: string) {
    super(`${code}: ${file}`);
    this.name = "ModelIntegrityError";
    this.code = code;
    this.file = file;
  }
}

export type VerifiedModels = Record<ModelRole, ArrayBuffer>;

export interface ModelLoaderEnv {
  fetchImpl?: (url: string, init?: RequestInit) => Promise<Response>;
  digest?: (data: ArrayBuffer) => Promise<ArrayBuffer>;
  /** The page origin (defaults to `window.location.origin`). */
  origin?: string;
  manifest?: ModelManifest;
}

export function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** True when `path` resolves to the page's own origin. */
export function isSameOrigin(path: string, origin: string): boolean {
  try {
    return new URL(path, origin).origin === new URL(origin).origin;
  } catch {
    return false;
  }
}

function defaultDigest(): ((data: ArrayBuffer) => Promise<ArrayBuffer>) | null {
  const subtle = typeof crypto !== "undefined" ? crypto.subtle : undefined;
  if (!subtle) return null;
  return (data) => subtle.digest("SHA-256", data);
}

export async function loadVerifiedModels(env: ModelLoaderEnv = {}): Promise<VerifiedModels> {
  const manifest = env.manifest ?? MODEL_MANIFEST;
  const origin = env.origin ?? window.location.origin;
  const fetchImpl = env.fetchImpl ?? ((url: string, init?: RequestInit) => fetch(url, init));
  const digest = env.digest ?? defaultDigest();
  if (!digest) throw new ModelIntegrityError("model_unsupported", "crypto.subtle");

  // Validate the whole manifest before the first request.
  for (const role of MODEL_ROLES) {
    const entry = manifest.models.find((m) => m.role === role);
    if (!entry) throw new ModelIntegrityError("model_missing", role);
    if (!isSameOrigin(modelUrl(entry, manifest), origin)) {
      throw new ModelIntegrityError("model_cross_origin", entry.file);
    }
    if (!/^[0-9a-f]{64}$/.test(entry.sha256)) throw new ModelIntegrityError("model_unpinned", entry.file);
  }

  const result: Partial<VerifiedModels> = {};
  for (const role of MODEL_ROLES) {
    const entry = manifest.models.find((m) => m.role === role)!;
    let response: Response;
    try {
      response = await fetchImpl(modelUrl(entry, manifest), {
        credentials: "same-origin",
        cache: "no-cache",
      });
    } catch {
      throw new ModelIntegrityError("model_missing", entry.file);
    }
    if (!response.ok) throw new ModelIntegrityError("model_missing", entry.file);
    const bytes = await response.arrayBuffer();
    const hash = toHex(await digest(bytes));
    if (hash !== entry.sha256) throw new ModelIntegrityError("model_hash_mismatch", entry.file);
    result[role] = bytes;
  }
  return result as VerifiedModels;
}
