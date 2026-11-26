import type { NextRequest } from "next/server";
import { notFound, proxyDelete, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ entityId: string }>;
}

// Phase 13 D11: change or remove one allowlisted Home Assistant entity.
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { entityId } = await params;
  if (!validSegments([entityId])) return notFound();
  return proxyMutation(request, "PATCH", `/connectors/home-assistant/entities/${entityId}`);
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { entityId } = await params;
  if (!validSegments([entityId])) return notFound();
  return proxyDelete(request, `/connectors/home-assistant/entities/${entityId}`);
}
