import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ skillId: string }>;
}

// Phase 12 D14: an edit is a new proposed version (the current stays).
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { skillId } = await params;
  if (!validSegments([skillId])) return notFound();
  return proxyMutation(request, "POST", `/skills/${skillId}/versions`);
}
