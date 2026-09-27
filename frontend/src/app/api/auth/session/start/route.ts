import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { clearAuthCookies, clearDeviceCookie } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import { remintFromDevice } from "@/lib/device-session";

export const runtime = "nodejs";

/**
 * Start a session from this browser's trusted-device cookie (Phase 4
 * branch 2). `/enroll` calls it first: an already-trusted browser whose
 * session lapsed goes straight back to the app instead of asking for a
 * code. 401 means "not a trusted device (any more)" -> show the enroll form.
 */
export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const result = await remintFromDevice(request);
  if (result === "ok") {
    return NextResponse.json({ ok: true });
  }
  if (result === "unavailable") {
    return NextResponse.json({ detail: "Session service unavailable." }, { status: 503 });
  }
  if (result === "rejected") {
    await clearDeviceCookie();
  }
  await clearAuthCookies();
  return NextResponse.json({ detail: "This browser is not a trusted device.", code: result }, {
    status: 401,
  });
}
