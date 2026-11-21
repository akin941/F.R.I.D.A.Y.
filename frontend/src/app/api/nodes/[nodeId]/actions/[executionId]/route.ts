import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ nodeId: string; executionId: string }>;
}

/** Read-only: one node action execution's status (Phase 10 D8). */
export async function GET(_request: NextRequest, { params }: RouteParams) {
  const { nodeId, executionId } = await params;
  if (!isValidUuid(nodeId) || !isValidUuid(executionId)) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const response = await backendFetch(`/nodes/${nodeId}/actions/${executionId}`, { accessToken });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
