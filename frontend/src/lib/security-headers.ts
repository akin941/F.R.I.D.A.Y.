/**
 * Browser-facing security headers (Phase 4 branch 1). Django sits behind
 * this BFF on loopback HTTP and never talks to a browser, so HSTS,
 * framing, sniffing and referrer policy are set here. Production builds
 * only: `next dev` serves plain http://localhost, where HSTS is ignored
 * anyway and strict framing would only get in the way of tooling.
 *
 * Imported by next.config.ts, so it must stay free of `server-only` and
 * `@/` path aliases.
 */
export interface HeaderRule {
  source: string;
  headers: { key: string; value: string }[];
}

export const SECURITY_HEADERS = [
  // Honored only over HTTPS (a TLS reverse proxy in front of `next start`);
  // no includeSubDomains/preload -- the owner's domain may host other apps.
  { key: "Strict-Transport-Security", value: "max-age=31536000" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "same-origin" },
  // Phase 6 branch 4 (D6): only Friday's own origin may ask for the mic
  // (push-to-talk and wake mode); camera and location are never used.
  { key: "Permissions-Policy", value: "microphone=(self), camera=(), geolocation=()" },
];

// The CSP is `frame-ancestors` only. If a `script-src` is ever added it must
// include 'wasm-unsafe-eval' (the wake-word Worker runs onnxruntime-web's
// WASM build) and a `worker-src 'self' blob:` (the capture worklet loads from
// a Blob URL). Multi-threaded WASM would also need COOP/COEP; the
// single-threaded build used here does not (D6 "WASM threading").

export function securityHeaderRules(isProduction: boolean): HeaderRule[] {
  return isProduction ? [{ source: "/:path*", headers: SECURITY_HEADERS }] : [];
}
