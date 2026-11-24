import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { notFound, proxyMutation, workspaceSuffix } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ kind: string }>;
}

const KINDS = new Set(["morning_digest", "heartbeat"]);

// Phase 12 D3: create/update the workspace's one schedule of `kind`.
export async function PUT(request: NextRequest, { params }: RouteParams) {
  const { kind } = await params;
  if (!KINDS.has(kind)) return notFound();
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;
  return proxyMutation(request, "PUT", `/proactive/schedules/${kind}${suffix}`);
}
