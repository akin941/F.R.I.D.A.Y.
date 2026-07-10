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
  let name: unknown;
  try {
    ({ email, password, name } = await request.json());
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  if (typeof email !== "string" || typeof password !== "string") {
    return NextResponse.json(
      { detail: "email and password are required." },
      { status: 400 },
    );
  }

  const signupResponse = await backendFetch("/auth/signup", {
    method: "POST",
    body: { email, password, name: typeof name === "string" ? name : "" },
  });
  if (!signupResponse.ok) {
    const body = (await signupResponse.json().catch(() => ({}))) as ApiErrorBody;
    return NextResponse.json(body, { status: signupResponse.status });
  }
  const data = (await signupResponse.json()) as {
    tokens: { access: string; refresh: string };
  };

  const meResponse = await backendFetch("/auth/me", { accessToken: data.tokens.access });
  if (!meResponse.ok) {
    return NextResponse.json(
      { detail: "Signup succeeded but session lookup failed." },
      { status: 502 },
    );
  }
  const session = (await meResponse.json()) as SessionResponse;

  await setAuthCookies(data.tokens);
  return NextResponse.json(session, { status: 201 });
}
