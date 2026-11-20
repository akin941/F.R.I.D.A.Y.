import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ kind: string; itemId: string; action: string }>;
}

// Per-item decisions on a learned proposal (D10). A memory proposal takes
// approve | edit (with optional curated fields) | reject; a core-block
// proposal takes approve | reject only (no body). Anything else is a 404
// before the backend is reached.
const ACTIONS: Record<string, Record<string, boolean>> = {
  memory: { approve: true, edit: true, reject: false },
  "core-block": { approve: false, reject: false },
};

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { kind, itemId, action } = await params;
  const actions = ACTIONS[kind];
  if (!actions || !Object.hasOwn(actions, action) || !validSegments([itemId])) return notFound();
  return proxyMutation(
    request,
    "POST",
    `/memory/learning/proposals/${kind}/${itemId}/${action}`,
    { withBody: actions[action] },
  );
}
