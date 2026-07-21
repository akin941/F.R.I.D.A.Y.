import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { clearAuthCookies, getRefreshToken, setAuthCookies } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const refreshToken = await getRefreshToken();
  if (!refreshToken) {
    await clearAuthCookies();
    return NextResponse.json({ detail: "No refresh token." }, { status: 401 });
  }

  const refreshResponse = await backendFetch("/auth/refresh", {
    method: "POST",
    body: { refresh: refreshToken },
  });
  if (!refreshResponse.ok) {
    await clearAuthCookies();
    return NextResponse.json({ detail: "Session expired." }, { status: 401 });
  }
  const data = (await refreshResponse.json()) as { access: string; refresh?: string };
  await setAuthCookies({ access: data.access, refresh: data.refresh });
  return NextResponse.json({ ok: true });
}
