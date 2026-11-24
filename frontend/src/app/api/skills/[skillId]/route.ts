import type { NextRequest } from "next/server";
import { notFound, proxyGet, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ skillId: string }>;
}

// Phase 12 D20: one skill and every version (full text, for review).
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { skillId } = await params;
  if (!validSegments([skillId])) return notFound();
  return proxyGet(request, `/skills/${skillId}`);
}
