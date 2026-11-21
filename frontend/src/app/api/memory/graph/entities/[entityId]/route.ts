import type { NextRequest } from "next/server";
import { notFound, proxyDelete, proxyGet, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ entityId: string }>;
}

// The entity with its neighbourhood, fact count and Graphify snapshot.
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { entityId } = await params;
  if (!validSegments([entityId])) return notFound();
  return proxyGet(request, `/memory/graph/entities/${entityId}`, { optional: ["hops", "as_of"] });
}

// Owner trash of the entity and its learned facts (ADR-009; Graphify -> 405).
export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { entityId } = await params;
  if (!validSegments([entityId])) return notFound();
  return proxyDelete(request, `/memory/graph/entities/${entityId}`);
}
