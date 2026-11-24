import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ digestId: string }>;
}

// Phase 12 D6: `{"event": "opened" | "spoken"}` -- the digest's own telemetry.
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { digestId } = await params;
  if (!validSegments([digestId])) return notFound();
  return proxyMutation(request, "POST", `/proactive/digests/${digestId}/ack`);
}
