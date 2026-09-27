import "server-only";
import { isIP } from "node:net";
import type { NextRequest } from "next/server";
import { env } from "@/lib/env";

/**
 * The browser's IP for Django's device-pairing LAN gating (Phase 4 branch 2).
 *
 * Step-0 finding (brief, 2026-09-27): a Next.js 16 route handler never sees
 * the socket address, and `next dev` / `next start` only *fill in*
 * `x-forwarded-for` when the request lacks one -- so without a reverse proxy
 * both `x-forwarded-for` and `x-real-ip` are client-controlled. The IP is
 * therefore read ONLY from the header the operator names in
 * `TRUSTED_CLIENT_IP_HEADER` (set by their proxy, e.g. Caddy's
 * `header_up X-Real-IP {remote_host}`), taking the right-most entry of a
 * list-valued header (the one the trusted proxy appended). It is forwarded
 * as `X-Jarvis-Client-IP` together with `X-Jarvis-BFF-Key`; Django ignores
 * the IP without a matching key. No configured header, no shared secret, or
 * an unparseable value -> nothing is forwarded (Django: "unknown IP").
 */
export function trustedClientIp(request: NextRequest): string | null {
  const headerName = env.TRUSTED_CLIENT_IP_HEADER.trim();
  if (!headerName) return null;
  const raw = request.headers.get(headerName);
  if (!raw) return null;
  const candidate = raw.split(",").at(-1)?.trim() ?? "";
  return isIP(candidate) ? candidate : null;
}

export function clientIpHeaders(request: NextRequest): Record<string, string> {
  const secret = env.JARVIS_BFF_SHARED_SECRET;
  const ip = trustedClientIp(request);
  if (!secret || !ip) return {};
  return { "X-Jarvis-Client-IP": ip, "X-Jarvis-BFF-Key": secret };
}
