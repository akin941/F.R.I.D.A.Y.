import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { env } from "@/lib/env";
import { getAccessToken } from "@/lib/auth-cookies";

export const runtime = "nodejs";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * BFF for the HUD event stream (Phase 6 A8): backend
 * `GET /api/v1/hud/events?workspace=<uuid>[&date=YYYY-MM-DD]`, streamed
 * through untouched (the chat route's pattern -- `backendFetch` always asks
 * for JSON and buffers). Read-only: GET only, no body. Only the two known
 * query parameters are forwarded, each validated first. The client's abort
 * (HUD unmount, tab close) cancels the upstream stream.
 */
export async function GET(request: NextRequest) {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated.", code: "not_authenticated" }, { status: 401 });
  }

  const workspace = request.nextUrl.searchParams.get("workspace") ?? "";
  const date = request.nextUrl.searchParams.get("date");
  if (!UUID.test(workspace) || (date !== null && !DATE.test(date))) {
    return NextResponse.json({ detail: "Invalid query.", code: "invalid" }, { status: 400 });
  }
  const query = new URLSearchParams({ workspace });
  if (date) query.set("date", date);

  let upstream: Response;
  try {
    upstream = await fetch(`${env.BACKEND_API_URL}/hud/events?${query.toString()}`, {
      method: "GET",
      headers: { Accept: "text/event-stream", Authorization: `Bearer ${accessToken}` },
      signal: request.signal,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "Backend unreachable.", code: "backend_unavailable" }, { status: 502 });
  }

  if (!upstream.ok || !upstream.body) {
    const body = await upstream.json().catch(() => ({}));
    const headers: Record<string, string> = {};
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) headers["Retry-After"] = retryAfter;
    return NextResponse.json(body, { status: upstream.status, headers });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-store",
      "X-Accel-Buffering": "no",
    },
  });
}
