import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { setAuthCookies } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import type { ApiErrorBody, SessionResponse } from "@/types/api";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  let email: unknown;
  let password: unknown;
  try {
    ({ email, password } = await request.json());
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  if (typeof email !== "string" || typeof password !== "string") {
    return NextResponse.json(
      { detail: "email and password are required." },
      { status: 400 },
    );
  }

  const loginResponse = await backendFetch("/auth/login", {
    method: "POST",
    body: { email, password },
  });
  if (!loginResponse.ok) {
    const body = (await loginResponse.json().catch(() => ({}))) as ApiErrorBody;
    return NextResponse.json(body, { status: loginResponse.status });
  }
  const tokens = (await loginResponse.json()) as { access: string; refresh: string };

  const meResponse = await backendFetch("/auth/me", { accessToken: tokens.access });
  if (!meResponse.ok) {
    return NextResponse.json(
      { detail: "Login succeeded but session lookup failed." },
      { status: 502 },
    );
  }
  const session = (await meResponse.json()) as SessionResponse;

  // Cookies are written last, only once every upstream call has succeeded —
  // a partial failure above must not leave the browser holding a session cookie.
  await setAuthCookies(tokens);
  return NextResponse.json(session, { status: 200 });
}
