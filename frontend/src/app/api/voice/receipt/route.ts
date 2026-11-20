import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

/**
 * `GET /api/voice/receipt` (Phase 6 A7): the "bu ne kadar tuttu?" voice
 * route's ModelUsage receipt -- a read. Only the two UUID query params are
 * forwarded; the backend scopes the conversation through `for_actor()`.
 */
export async function GET(request: NextRequest) {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const conversation = request.nextUrl.searchParams.get("conversation") ?? "";
  const trace = request.nextUrl.searchParams.get("trace") ?? "";
  if (!isValidUuid(conversation) || (trace && !isValidUuid(trace))) {
    return NextResponse.json({ detail: "Invalid query.", code: "invalid" }, { status: 400 });
  }
  const query = new URLSearchParams({ conversation });
  if (trace) query.set("trace", trace);
  const response = await backendFetch(`/voice/receipt?${query.toString()}`, {
    accessToken,
    signal: request.signal,
  });
  const body = await response.json().catch(() => ({}));
  const headers: Record<string, string> = { "Cache-Control": "no-store" };
  const retryAfter = response.headers.get("retry-after");
  if (retryAfter) headers["Retry-After"] = retryAfter;
  return NextResponse.json(body, { status: response.status, headers });
}
