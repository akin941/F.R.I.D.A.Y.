import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ sourceId: string; action: string }>;
}

const ACTIONS = new Set(["forget", "restore"]);

// Forget / restore a source (a reversible confirm-class trash move, ADR-009).
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { sourceId, action } = await params;
  if (!ACTIONS.has(action) || !validSegments([sourceId])) return notFound();
  return proxyMutation(request, "POST", `/memory/ingestion/sources/${sourceId}/${action}`, {
    withBody: false,
  });
}
