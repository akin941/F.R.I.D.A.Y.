import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ entityId: string }>;
}

// Undo an owner trash of the entity (and exactly the facts it moved).
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { entityId } = await params;
  if (!validSegments([entityId])) return notFound();
  return proxyMutation(request, "POST", `/memory/graph/entities/${entityId}/restore`, { withBody: false });
}
