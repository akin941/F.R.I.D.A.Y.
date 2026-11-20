import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ targetId: string }>;
}

// Queue a read-only `status` verb on the runner (202, no body).
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { targetId } = await params;
  if (!validSegments([targetId])) return notFound();
  return proxyMutation(request, "POST", `/deploys/targets/${targetId}/status`, {
    withBody: false,
  });
}
