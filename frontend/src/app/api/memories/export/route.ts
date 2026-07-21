import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";

export const runtime = "nodejs";

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

  const response = await backendFetch(
    `/memories/export?workspace=${encodeURIComponent(workspace)}`,
    { accessToken },
  );
  const body = await response.json().catch(() => ({}));
  const headers: Record<string, string> = {
    // The export can contain private memory content — never cached, at any
    // layer, browser or intermediary (mirrors the backend's own header).
    "Cache-Control": "no-store",
  };
  const contentDisposition = response.headers.get("content-disposition");
  if (contentDisposition) {
    headers["Content-Disposition"] = contentDisposition;
  }
  return NextResponse.json(body, { status: response.status, headers });
}
