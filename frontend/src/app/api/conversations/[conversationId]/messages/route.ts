import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { env } from "@/lib/env";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ conversationId: string }>;
}

export async function GET(_request: NextRequest, { params }: RouteParams) {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const { conversationId } = await params;

  const response = await backendFetch(`/ai/conversations/${conversationId}/messages`, {
    accessToken,
  });
  const body = await response.json().catch(() => []);
  return NextResponse.json(body, { status: response.status });
}

export async function POST(request: NextRequest, { params }: RouteParams) {
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

  const { conversationId } = await params;

  // Bypasses backendFetch: that helper always sends Accept: application/json,
  // but the backend only serves an SSE body when Accept: text/event-stream is
  // present, and the success path here must stream upstream.body through
  // untouched rather than buffer it as JSON.
  const upstream = await fetch(
    `${env.BACKEND_API_URL}/ai/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(payload),
      signal: request.signal,
      cache: "no-store",
    },
  );

  if (!upstream.ok || !upstream.body) {
    const body = await upstream.json().catch(() => ({}));
    const headers: Record<string, string> = {};
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) {
      headers["Retry-After"] = retryAfter;
    }
    return NextResponse.json(body, { status: upstream.status, headers });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    },
  });
}
