import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ nodeId: string }>;
}

/**
 * Read-only: the node's chain rows (Phase 9 D11), newest first, 50 a page.
 * Only a numeric `cursor` (a chain `seq`) is forwarded.
 */
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { nodeId } = await params;
  if (!isValidUuid(nodeId)) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }
  const cursor = request.nextUrl.searchParams.get("cursor");
  if (cursor !== null && !/^\d{1,18}$/.test(cursor)) {
    return NextResponse.json({ detail: "Invalid cursor.", code: "invalid" }, { status: 400 });
  }
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const query = cursor !== null ? `?cursor=${cursor}` : "";
  const response = await backendFetch(`/nodes/${nodeId}/invocations${query}`, { accessToken });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
