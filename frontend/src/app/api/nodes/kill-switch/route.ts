import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

/**
 * BFF for the owner's node kill switch (Phase 9 D1/D11): backend
 * `GET/POST/DELETE /api/v1/auth/nodes/kill-switch` -- state, engage,
 * release. No body is forwarded.
 */
async function forward(method: "GET" | "POST" | "DELETE") {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const response = await backendFetch("/auth/nodes/kill-switch", { method, accessToken });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}

function rejectUnsafe(request: NextRequest) {
  return rejectCrossOrigin(request) ?? rejectNonJson(request);
}

export async function GET() {
  return forward("GET");
}

export async function POST(request: NextRequest) {
  return rejectUnsafe(request) ?? forward("POST");
}

export async function DELETE(request: NextRequest) {
  return rejectUnsafe(request) ?? forward("DELETE");
}
