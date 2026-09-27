import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";

export const runtime = "nodejs";

// The workspace's trashed knowledge documents (ADR-009 "Çöp kutusu"),
// newest deletion first.
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
  const response = await backendFetch(`/knowledge/documents/trash?${query.toString()}`, { accessToken });
  const body = await response.json().catch(() => []);
  return NextResponse.json(body, { status: response.status });
}
