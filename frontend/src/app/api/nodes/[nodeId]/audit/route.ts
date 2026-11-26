import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { notFound, proxyGet, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ nodeId: string }>;
}

/**
 * Read-only: the node's audit (Phase 13 D8) -- chain rows merged with its
 * audit events, newest first, 50 a page. Only a well-formed `before`
 * cursor (`<microseconds>.<uuid>`) is forwarded.
 */
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { nodeId } = await params;
  if (!validSegments([nodeId])) return notFound();
  const before = request.nextUrl.searchParams.get("before");
  if (before !== null && !/^\d{1,18}\.[0-9a-f-]{36}$/i.test(before)) {
    return NextResponse.json({ detail: "Invalid cursor.", code: "invalid" }, { status: 400 });
  }
  return proxyGet(request, `/nodes/${nodeId}/audit`, { optional: ["before"] });
}
