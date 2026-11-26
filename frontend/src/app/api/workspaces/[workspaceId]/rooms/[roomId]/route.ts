import type { NextRequest } from "next/server";
import { notFound, proxyDelete, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ workspaceId: string; roomId: string }>;
}

// Phase 13 D2: rename / re-alias / reorder or delete a room.
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { workspaceId, roomId } = await params;
  if (!validSegments([workspaceId, roomId])) return notFound();
  return proxyMutation(request, "PATCH", `/workspaces/${workspaceId}/rooms/${roomId}`);
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { workspaceId, roomId } = await params;
  if (!validSegments([workspaceId, roomId])) return notFound();
  return proxyDelete(request, `/workspaces/${workspaceId}/rooms/${roomId}`);
}
