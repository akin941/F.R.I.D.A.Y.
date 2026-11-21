import type { NextRequest } from "next/server";
import { notFound, proxyGet, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ entityId: string }>;
}

// The entity's timeline (current, superseded and pending), or the facts valid `as_of`.
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { entityId } = await params;
  if (!validSegments([entityId])) return notFound();
  return proxyGet(request, `/memory/graph/entities/${entityId}/facts`, { optional: ["as_of"] }, []);
}
