import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import { env } from "@/lib/env";
import { traceHeaders } from "@/lib/voice-trace";

export const runtime = "nodejs";

/**
 * `POST /api/voice/speech` (Phase 6 D1/D7): one reply sentence -> the
 * backend's checked `audio/wav`, streamed back to the browser unmodified.
 * Errors come back as the backend's JSON body (stable `code`).
 */
export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  // Bypasses backendFetch: the success body is binary audio streamed
  // through untouched, not JSON.
  const upstream = await fetch(`${env.BACKEND_API_URL}/voice/speech`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "audio/wav",
      Authorization: `Bearer ${accessToken}`,
      ...traceHeaders(request),
    },
    body: JSON.stringify(payload),
    signal: request.signal,
    cache: "no-store",
  });

  const contentType = upstream.headers.get("content-type") ?? "";
  if (!upstream.ok || !upstream.body || !contentType.startsWith("audio/wav")) {
    const body = await upstream.json().catch(() => ({}));
    const headers: Record<string, string> = {};
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) headers["Retry-After"] = retryAfter;
    // A 200 that is not audio/wav is never passed on as audio.
    const status = upstream.ok ? 502 : upstream.status;
    return NextResponse.json(body, { status, headers });
  }

  const headers: Record<string, string> = {
    "Content-Type": "audio/wav",
    "Cache-Control": "no-store",
  };
  const length = upstream.headers.get("content-length");
  if (length) headers["Content-Length"] = length;
  return new Response(upstream.body, { status: upstream.status, headers });
}
