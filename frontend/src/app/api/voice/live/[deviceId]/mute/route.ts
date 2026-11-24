import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ deviceId: string }>;
}

/**
 * `DELETE /api/voice/live/{deviceId}/mute` (Phase 11 D12): release a node's
 * storm mute -- only ever from the owner's screen. Backend
 * `DELETE /api/v1/auth/nodes/{device_id}/voice-mute`; no body is forwarded.
 */
export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;
  const { deviceId } = await params;
  if (!isValidUuid(deviceId)) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }
  const response = await backendFetch(`/auth/nodes/${deviceId}/voice-mute`, {
    method: "DELETE",
    accessToken,
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
