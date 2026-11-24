import type { NextRequest } from "next/server";
import { notFound, proxyGet, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ digestId: string }>;
}

// Phase 12 D6: one digest.
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { digestId } = await params;
  if (!validSegments([digestId])) return notFound();
  return proxyGet(request, `/proactive/digests/${digestId}`);
}
