import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { clearAuthCookies } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  // The backend has no logout/blacklist endpoint (MVP 1 scope); clearing the
  // cookies is the entire logout — do not invent token revocation here.
  await clearAuthCookies();
  return NextResponse.json({ ok: true });
}
