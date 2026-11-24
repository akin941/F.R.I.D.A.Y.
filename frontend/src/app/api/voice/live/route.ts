import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";

export const runtime = "nodejs";

/**
 * `GET /api/voice/live` (Phase 11 D12/D15): the "Canlı ses (Mac)" panel's
 * data -- backend `GET /api/v1/auth/nodes/voice-live` (web session, owner):
 * the always-on gate's verdict per measured wake model and each node's
 * storm mute. A read; nothing is forwarded but the session.
 */
export async function GET() {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const response = await backendFetch("/auth/nodes/voice-live", { accessToken });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, {
    status: response.status,
    headers: { "Cache-Control": "no-store" },
  });
}
