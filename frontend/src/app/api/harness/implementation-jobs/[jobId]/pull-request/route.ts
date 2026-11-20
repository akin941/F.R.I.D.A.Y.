import type { NextRequest } from "next/server";
import { notFound, proxyGet, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ jobId: string }>;
}

// The job's PR/CI panel (GET) and "PR aç" (POST {title?}), Phase 5 branch 5.
export async function GET(request: NextRequest, { params }: RouteParams) {
  const { jobId } = await params;
  if (!validSegments([jobId])) return notFound();
  return proxyGet(request, `/harness/implementation-jobs/${jobId}/pull-request`);
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { jobId } = await params;
  if (!validSegments([jobId])) return notFound();
  return proxyMutation(request, "POST", `/harness/implementation-jobs/${jobId}/pull-request`);
}
