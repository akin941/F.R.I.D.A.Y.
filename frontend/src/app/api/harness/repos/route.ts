import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

// The repo registry (Phase 4 branch 5): list for the picker and the
// registry page, create from the registry page. Curator gating stays in
// the backend's harness services.
export async function GET(request: NextRequest) {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  const workspace = request.nextUrl.searchParams.get("workspace");
  if (!workspace) {
    return NextResponse.json(
      { detail: "workspace query parameter is required." },
      { status: 400 },
    );
  }

  const query = new URLSearchParams({ workspace });
  const response = await backendFetch(`/harness/repos?${query.toString()}`, { accessToken });
  const body = await response.json().catch(() => []);
  return NextResponse.json(body, { status: response.status });
}

export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  const response = await backendFetch("/harness/repos", {
    method: "POST",
    accessToken,
    body: payload,
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
