import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";

export const runtime = "nodejs";

/**
 * Read-only proxy for the shell's badge feed (backend
 * `GET /api/v1/activity/summary`: pending approvals and live harness/agent
 * counts per workspace membership). An optional `workspace` narrows it.
 */
export async function GET(request: NextRequest) {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json(
      { detail: "Not authenticated.", code: "not_authenticated" },
      { status: 401 },
    );
  }

  const query = new URLSearchParams();
  const workspace = request.nextUrl.searchParams.get("workspace");
  if (workspace) query.set("workspace", workspace);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";

  const response = await backendFetch(`/activity/summary${suffix}`, { accessToken });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
