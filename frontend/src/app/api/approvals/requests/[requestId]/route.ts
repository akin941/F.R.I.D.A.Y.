import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { isValidUuid } from "@/lib/uuid";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ requestId: string }>;
}

// Read-only: one approval request for `/approvals/[requestId]`. Deciding
// stays on the `[requestId]/[action]` route.
export async function GET(_request: NextRequest, { params }: RouteParams) {
  const { requestId } = await params;
  if (!isValidUuid(requestId)) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  const response = await backendFetch(`/approvals/requests/${requestId}`, { accessToken });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
