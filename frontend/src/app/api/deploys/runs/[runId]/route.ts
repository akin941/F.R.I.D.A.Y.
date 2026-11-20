import type { NextRequest } from "next/server";
import { notFound, proxyGet, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ runId: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { runId } = await params;
  if (!validSegments([runId])) return notFound();
  return proxyGet(request, `/deploys/runs/${runId}`);
}
