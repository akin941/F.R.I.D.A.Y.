import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

/**
 * "Mac eşleştir" (Phase 9 D1/D11): a one-time NODE pairing code, shown
 * once. Always `device_kind=node`; `kind` is `pin` (LAN only, default) or
 * `strong`. Nothing else from the body is forwarded.
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
    body: { kind, device_kind: "node" },
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
