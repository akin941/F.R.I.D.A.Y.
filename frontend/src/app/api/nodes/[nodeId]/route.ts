import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ nodeId: string }>;
}

/** Read-only: one node with its snapshot entries (Phase 9 D11). */
export async function GET(_request: NextRequest, { params }: RouteParams) {
  const { nodeId } = await params;
  if (!isValidUuid(nodeId)) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const response = await backendFetch(`/nodes/${nodeId}`, { accessToken });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}

/**
 * Phase 13 D3: the owner's labels (name, room, role, aliases). Server-side
 * data only -- nothing reaches the node; its policy stays its own.
 */
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { nodeId } = await params;
  if (!validSegments([nodeId])) return notFound();
  return proxyMutation(request, "PATCH", `/nodes/${nodeId}`);
}
