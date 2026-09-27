import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import {
  clearAuthCookies,
  clearDeviceCookie,
  getAccessToken,
  getRefreshToken,
} from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

/**
 * "Bu cihazı unut" (Phase 4 branch 2): revokes THIS device server-side, ends
 * the session and drops every auth cookie including the device secret. The
 * revoke must succeed before anything is cleared -- a failed revoke is
 * reported, never silently turned into a mere logout.
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
  const response = await backendFetch("/auth/devices/current/revoke", {
    method: "POST",
    accessToken,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    return NextResponse.json(body, { status: response.status });
  }
  const refreshToken = await getRefreshToken();
  if (refreshToken) {
    try {
      await backendFetch("/auth/logout", { method: "POST", body: { refresh: refreshToken } });
    } catch {
      // the device is already revoked; the refresh token can no longer be used
    }
  }
  await clearAuthCookies();
  await clearDeviceCookie();
  return NextResponse.json({ ok: true });
}
