import type { NextRequest } from "next/server";
import { notFound, proxyGet, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ sourceId: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { sourceId } = await params;
  if (!validSegments([sourceId])) return notFound();
  return proxyGet(request, `/memory/ingestion/sources/${sourceId}/rules`, {}, []);
}

// A new mapping rule: body {rule_type, pattern, target_workspace}.
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { sourceId } = await params;
  if (!validSegments([sourceId])) return notFound();
  return proxyMutation(request, "POST", `/memory/ingestion/sources/${sourceId}/rules`);
}
