import type { NextRequest } from "next/server";
import { notFound, proxyGet, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ targetId: string }>;
}

// The "Deploy" dialog's SHA prefill: tracked branch head + CI state.
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { targetId } = await params;
  if (!validSegments([targetId])) return notFound();
  return proxyGet(request, `/deploys/targets/${targetId}/head`);
}
