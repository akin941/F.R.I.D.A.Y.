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

const ALLOWED_KEYS = new Set(["capability", "args", "source_message_id"]);

/**
 * BFF for the confirm-class path (Phase 10 D8): backend
 * `POST /api/v1/nodes/<node_id>/actions` -- `apps.open` / `apps.focus`,
 * started by the owner's card click and confirmed on the Mac. Only the
 * three accepted keys are forwarded; the backend validates them again.
 */
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
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  const body = Object.fromEntries(
    Object.entries(payload as Record<string, unknown>).filter(([key]) => ALLOWED_KEYS.has(key)),
  );
  const response = await backendFetch(`/nodes/${nodeId}/actions`, {
    method: "POST",
    accessToken,
    body,
  });
  const result = await response.json().catch(() => ({}));
  return NextResponse.json(result, { status: response.status });
}
