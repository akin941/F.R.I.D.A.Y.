import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

/** "Yeni cihaz eşleştir": issue a one-time pairing code (shown once). */
export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  let payload: unknown = {};
  try {
    payload = await request.json();
  } catch {
    payload = {};
  }
  const kind =
    payload && typeof payload === "object" && (payload as { kind?: unknown }).kind === "strong"
      ? "strong"
      : "pin";
  const response = await backendFetch("/auth/pairing-codes", {
    method: "POST",
    accessToken,
    body: { kind },
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
