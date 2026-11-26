import type { NextRequest } from "next/server";
import { notFound, proxyGet, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ workspaceId: string }>;
}

// Phase 13 D2: the personal workspace's rooms (owner, web session only).
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { workspaceId } = await params;
  if (!validSegments([workspaceId])) return notFound();
  return proxyGet(request, `/workspaces/${workspaceId}/rooms`);
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { workspaceId } = await params;
  if (!validSegments([workspaceId])) return notFound();
  return proxyMutation(request, "POST", `/workspaces/${workspaceId}/rooms`);
}
