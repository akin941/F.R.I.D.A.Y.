import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { env } from "@/lib/env";
import { getAccessToken } from "@/lib/auth-cookies";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

const STREAM_ID = /^\d{1,20}-\d{1,20}$/;

interface RouteParams {
  params: Promise<{ executionId: string }>;
}

/**
 * BFF for the HUD terminal panel (Phase 10 D12): backend
 * `GET /api/v1/hud/terminal/<execution_id>/stream`, streamed through
 * untouched (the HUD events route's pattern). Read-only: GET only, no body,
 * no query. Only a well-formed `Last-Event-ID` (a Redis stream id) is
 * forwarded, so a reconnect resumes after the last chunk it saw. The
 * client's abort cancels the upstream stream.
 */
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { executionId } = await params;
  if (!isValidUuid(executionId)) {
    return NextResponse.json({ detail: "Not found.", code: "not_found" }, { status: 404 });
  }
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated.", code: "not_authenticated" }, { status: 401 });
  }

  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    Authorization: `Bearer ${accessToken}`,
  };
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId && STREAM_ID.test(lastEventId)) headers["Last-Event-ID"] = lastEventId;

  let upstream: Response;
  try {
    upstream = await fetch(`${env.BACKEND_API_URL}/hud/terminal/${executionId}/stream`, {
      method: "GET",
      headers,
      signal: request.signal,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "Backend unreachable.", code: "backend_unavailable" }, { status: 502 });
  }

  if (!upstream.ok || !upstream.body) {
    const body = await upstream.json().catch(() => ({}));
    const out: Record<string, string> = {};
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) out["Retry-After"] = retryAfter;
    return NextResponse.json(body, { status: upstream.status, headers: out });
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
