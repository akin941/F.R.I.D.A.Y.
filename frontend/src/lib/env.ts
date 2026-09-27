import "server-only";

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

// Defense-in-depth cap for the knowledge file-upload BFF proxy — not the
// source of truth (the backend's own KNOWLEDGE_MAX_UPLOAD_BYTES setting is).
// Default has generous headroom over the backend's own 1,000,000-byte
// default; if that backend setting is ever raised, this env var must be
// raised to match (kept above it, not equal, to allow for multipart framing
// overhead) or the proxy will start rejecting otherwise-valid uploads.
const DEFAULT_KNOWLEDGE_MAX_UPLOAD_BYTES = 4_000_000;

export const env = {
  BACKEND_API_URL: required("BACKEND_API_URL", process.env.BACKEND_API_URL),
  APP_ORIGIN: required("APP_ORIGIN", process.env.APP_ORIGIN),
  // Device pairing LAN gating (Phase 4 branch 2) -- see lib/client-ip.ts.
  // Both optional: without them no client IP is ever forwarded.
  TRUSTED_CLIENT_IP_HEADER: process.env.TRUSTED_CLIENT_IP_HEADER ?? "",
  JARVIS_BFF_SHARED_SECRET: process.env.JARVIS_BFF_SHARED_SECRET ?? "",
  KNOWLEDGE_MAX_UPLOAD_BYTES: process.env.KNOWLEDGE_MAX_UPLOAD_BYTES
    ? Number(process.env.KNOWLEDGE_MAX_UPLOAD_BYTES)
    : DEFAULT_KNOWLEDGE_MAX_UPLOAD_BYTES,
};
