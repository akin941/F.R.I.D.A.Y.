import type { NextRequest } from "next/server";
import { notFound, proxyGet, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ targetId: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { targetId } = await params;
  if (!validSegments([targetId])) return notFound();
  return proxyGet(request, `/deploys/targets/${targetId}`);
}

export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { targetId } = await params;
  if (!validSegments([targetId])) return notFound();
  return proxyMutation(request, "PATCH", `/deploys/targets/${targetId}`);
}
