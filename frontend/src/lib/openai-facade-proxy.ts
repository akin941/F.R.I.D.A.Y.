import "server-only";
import type { NextRequest } from "next/server";
import { env } from "@/lib/env";
import { trustedClientIp } from "@/lib/client-ip";

/**
 * BFF passthrough for the OpenAI-compatible facade (Phase 4 branch 6):
 * public `/v1/chat/completions` and `/v1/models` -> Django
 * `/api/v1/openai/...`, so a client's base URL is `https://<host>/v1`.
 *
 * Unlike every other BFF route this one is bearer-only: the client is a
 * server (Open WebUI's backend), not the browser. It forwards only
 * `Authorization` (and the optional conversation pin), streams the upstream
 * body through, never reads or forwards cookies and emits no CORS headers
 * -- a browser page on another origin gets nothing it can read.
 *
 * Pre-auth flood limit (plan-review round 1, MEDIUM): a request without a
 * `Bearer jpat_...` credential is answered here (401) and an upstream 401
 * counts as an auth failure. Once a source has too many failures in the
 * current one-minute window, its requests get 429 without reaching Django.
 * The source is the client IP when the operator's reverse proxy provides a
 * trusted header (`TRUSTED_CLIENT_IP_HEADER`, lib/client-ip.ts); otherwise
 * there is one install-wide bucket -- a flood of bad tokens then also blocks
 * valid ones until the window ends (the documented residual risk, the same
 * shape as device enrollment's global ceiling). Counters are per process.
 */

const WINDOW_MS = 60_000;
const DEFAULT_FAILURES_PER_SOURCE = 20;
const DEFAULT_FAILURES_GLOBAL = 60;
// Request bodies are buffered (they are small JSON documents); the backend's
// FACADE_MAX_REQUEST_CHARS is the real content limit.
const DEFAULT_MAX_BODY_BYTES = 4_000_000;
const CONVERSATION_HEADER = "x-friday-conversation-id";
// Pre-rename spelling (Jarvis -> Friday, 2026-11-20), still accepted from
// clients configured before the rename; always forwarded as the new name.
const LEGACY_CONVERSATION_HEADER = "x-jarvis-conversation-id";
const PAT_PREFIX = "jpat_";

function limit(name: string, fallback: number) {
  const raw = Number(process.env[name]);
  return Number.isFinite(raw) && raw > 0 ? raw : fallback;
}

interface Bucket {
  windowStart: number;
  failures: number;
}

const buckets = new Map<string, Bucket>();

/** Test hook: forget every failure counter. */
export function resetFacadeLimiter() {
  buckets.clear();
}

function sourceOf(request: NextRequest): { key: string; max: number } {
  const ip = trustedClientIp(request);
  if (ip) {
    const max = limit(
      "FACADE_PREAUTH_FAILURES_PER_SOURCE",
      DEFAULT_FAILURES_PER_SOURCE,
    );
    return { key: `ip:${ip}`, max };
  }
  const max = limit("FACADE_PREAUTH_FAILURES_GLOBAL", DEFAULT_FAILURES_GLOBAL);
  return { key: "global", max };
}

function currentBucket(key: string, now: number): Bucket {
  const bucket = buckets.get(key);
  if (!bucket || now - bucket.windowStart >= WINDOW_MS) {
    const fresh = { windowStart: now, failures: 0 };
    buckets.set(key, fresh);
    return fresh;
  }
  return bucket;
}

function openAiError(
  status: number,
  message: string,
  type: string,
  code: string,
  headers: Record<string, string> = {},
) {
  return new Response(
    JSON.stringify({ error: { message, type, param: null, code } }),
    {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    },
  );
}

function recordFailure(key: string) {
  currentBucket(key, Date.now()).failures += 1;
}

const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "retry-after",
  "x-friday-conversation-id",
  "x-request-id",
  "www-authenticate",
];

const tooLarge = () =>
  openAiError(
    413,
    "The request body is too large.",
    "invalid_request_error",
    "request_too_large",
  );

/**
 * Forward one facade request to Django. `backendPath` is relative to
 * BACKEND_API_URL (e.g. "/openai/models").
 */
export async function proxyFacadeRequest(
  request: NextRequest,
  backendPath: string,
): Promise<Response> {
  const now = Date.now();
  const source = sourceOf(request);
  const bucket = currentBucket(source.key, now);
  if (bucket.failures >= source.max) {
    const retryAfter = Math.max(
      1,
      Math.ceil((bucket.windowStart + WINDOW_MS - now) / 1000),
    );
    return openAiError(
      429,
      "Too many failed authentication attempts.",
      "rate_limit_error",
      "rate_limit_exceeded",
      { "Retry-After": String(retryAfter) },
    );
  }

  const authorization = request.headers.get("authorization") ?? "";
  const [scheme, token] = authorization.split(" ", 2);
  if (scheme?.toLowerCase() !== "bearer" || !token?.startsWith(PAT_PREFIX)) {
    recordFailure(source.key);
    return openAiError(
      401,
      "Invalid, expired or revoked API key.",
      "invalid_request_error",
      "invalid_api_key",
      { "WWW-Authenticate": "Bearer" },
    );
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: request.headers.get("accept") ?? "application/json",
  };
  const pin =
    request.headers.get(CONVERSATION_HEADER) ??
    request.headers.get(LEGACY_CONVERSATION_HEADER);
  if (pin) headers["X-Friday-Conversation-Id"] = pin;

  let body: string | undefined;
  if (request.method === "POST") {
    const maxBytes = limit("FACADE_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES);
    const declared = Number(request.headers.get("content-length") ?? "0");
    if (declared > maxBytes) return tooLarge();
    body = await request.text();
    if (new TextEncoder().encode(body).length > maxBytes) return tooLarge();
    headers["Content-Type"] = "application/json";
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${env.BACKEND_API_URL}${backendPath}`, {
      method: request.method,
      headers,
      body,
      signal: request.signal,
      cache: "no-store",
    });
  } catch {
    return openAiError(
      502,
      "Friday is not reachable right now.",
      "server_error",
      "upstream_unavailable",
    );
  }

  if (upstream.status === 401) {
    recordFailure(source.key);
  }

  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (responseHeaders.get("content-type")?.startsWith("text/event-stream")) {
    responseHeaders.set("Cache-Control", "no-cache");
    responseHeaders.set("X-Accel-Buffering", "no");
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
