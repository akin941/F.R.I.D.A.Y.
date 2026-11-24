import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ noticeId: string }>;
}

// Phase 12 D5: mark one notice read (it decides nothing).
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { noticeId } = await params;
  if (!validSegments([noticeId])) return notFound();
  return proxyMutation(request, "POST", `/proactive/notices/${noticeId}/read`, { withBody: false });
}
