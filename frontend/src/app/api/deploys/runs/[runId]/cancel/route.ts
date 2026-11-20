import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ runId: string }>;
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { runId } = await params;
  if (!validSegments([runId])) return notFound();
  return proxyMutation(request, "POST", `/deploys/runs/${runId}/cancel`, { withBody: false });
}
