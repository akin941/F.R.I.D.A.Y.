import type { NextRequest } from "next/server";
import { notFound, proxyGet, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ sourceId: string }>;
}

// The D11 audit view of one source (audited by the backend).
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { sourceId } = await params;
  if (!validSegments([sourceId])) return notFound();
  return proxyGet(request, `/memory/ingestion/sources/${sourceId}`);
}

// Enable / pause / daily budget.
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { sourceId } = await params;
  if (!validSegments([sourceId])) return notFound();
  return proxyMutation(request, "PATCH", `/memory/ingestion/sources/${sourceId}`);
}
