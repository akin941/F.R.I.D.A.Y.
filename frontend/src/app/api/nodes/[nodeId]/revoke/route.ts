import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ nodeId: string }>;
}

/** Revoke a node (Phase 9 D11): the backend delegates to `revoke_device`. */
export async function POST(request: NextRequest, { params }: RouteParams) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;
  const { nodeId } = await params;
  if (!isValidUuid(nodeId)) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const response = await backendFetch(`/nodes/${nodeId}/revoke`, { method: "POST", accessToken });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
