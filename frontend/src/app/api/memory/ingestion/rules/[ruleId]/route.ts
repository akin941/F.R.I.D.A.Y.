import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ ruleId: string }>;
}

// Enable/disable a mapping rule or retarget it: body {enabled?, target_workspace?}.
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { ruleId } = await params;
  if (!validSegments([ruleId])) return notFound();
  return proxyMutation(request, "PATCH", `/memory/ingestion/rules/${ruleId}`);
}
