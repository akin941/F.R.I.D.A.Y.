import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ memoryId: string; action: string }>;
}

const ALLOWED_ACTIONS = new Set(["approve", "reject", "restore", "forget"]);

export async function POST(request: NextRequest, { params }: RouteParams) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const { memoryId, action } = await params;
  if (!isValidUuid(memoryId) || !ALLOWED_ACTIONS.has(action)) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  let payload: unknown = {};
  try {
    const text = await request.text();
    payload = text ? JSON.parse(text) : {};
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  const response = await backendFetch(`/memories/${memoryId}/${action}`, {
    method: "POST",
    accessToken,
    body: payload,
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
