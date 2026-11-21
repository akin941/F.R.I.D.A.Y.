import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ factId: string; action: string }>;
}

// `supersede` carries a body (valid_from + the new object); the others none.
const ACTIONS = new Set(["approve", "reject", "supersede", "restore"]);

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { factId, action } = await params;
  if (!ACTIONS.has(action) || !validSegments([factId])) return notFound();
  return proxyMutation(request, "POST", `/memory/graph/facts/${factId}/${action}`, {
    withBody: action === "supersede",
  });
}
