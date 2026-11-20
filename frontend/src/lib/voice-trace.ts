import "server-only";
import type { NextRequest } from "next/server";
import { isValidUuid } from "@/lib/uuid";

/**
 * The per-turn voice trace id (Phase 6 A5): the browser sends one UUID per
 * spoken turn as `X-Friday-Trace` on the transcription, message and speech
 * requests; the BFF forwards it to Django only when it is a UUID (the
 * backend ignores anything else too). A correlation id, never trust.
 */
export const TRACE_HEADER = "X-Friday-Trace";

export function traceHeaders(request: NextRequest): Record<string, string> {
  const value = request.headers.get(TRACE_HEADER)?.trim() ?? "";
  return value && isValidUuid(value) ? { [TRACE_HEADER]: value } : {};
}
