import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { clearAuthCookies, getRefreshToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  // End the session server-side: the backend blacklists the refresh token
  // so it can never mint another access token (Phase 4 branch 1). Sent
  // without the access token -- it may already be expired, and the refresh
  // token itself is the credential here. Best effort: an unreachable
  // backend or an already-invalid token must never keep the browser
  // logged in, so the cookies are cleared regardless.
  const refreshToken = await getRefreshToken();
  if (refreshToken) {
    try {
      await backendFetch("/auth/logout", { method: "POST", body: { refresh: refreshToken } });
    } catch {
      // backend unreachable -- the token still expires on its own
    }
  }
  await clearAuthCookies();
  return NextResponse.json({ ok: true });
}
