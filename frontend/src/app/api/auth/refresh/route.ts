import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { clientIpHeaders } from "@/lib/client-ip";
import {
  clearAuthCookies,
  clearDeviceCookie,
  getRefreshToken,
  setAuthCookies,
} from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import { remintFromDevice } from "@/lib/device-session";

export const runtime = "nodejs";

/**
 * Rotate the refresh token; when that fails (expired, blacklisted, missing),
 * silently re-mint a session from the trusted-device cookie (Phase 4
 * branch 2). Only a rejected device sends the browser to /enroll.
 */
export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const refreshToken = await getRefreshToken();
  if (refreshToken) {
    const refreshResponse = await backendFetch("/auth/refresh", {
      method: "POST",
      body: { refresh: refreshToken },
      headers: clientIpHeaders(request),
    });
    if (refreshResponse.ok) {
      const data = (await refreshResponse.json()) as { access: string; refresh?: string };
      await setAuthCookies({ access: data.access, refresh: data.refresh });
      return NextResponse.json({ ok: true });
    }
  }

  const result = await remintFromDevice(request);
  if (result === "ok") {
    return NextResponse.json({ ok: true });
  }
  await clearAuthCookies();
  if (result === "rejected") {
    await clearDeviceCookie();
  }
  return NextResponse.json({ detail: "Session expired.", code: result }, { status: 401 });
}
