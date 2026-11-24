import type { NextRequest } from "next/server";
import { notFound, proxyDelete, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ aliasId: string }>;
}

// Phase 12 D8: the owner rewords or removes an alias.
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { aliasId } = await params;
  if (!validSegments([aliasId])) return notFound();
  return proxyMutation(request, "PATCH", `/assistant/wake-routes/aliases/${aliasId}`);
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { aliasId } = await params;
  if (!validSegments([aliasId])) return notFound();
  return proxyDelete(request, `/assistant/wake-routes/aliases/${aliasId}`);
}
