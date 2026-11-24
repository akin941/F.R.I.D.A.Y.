import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { proxyMutation, workspaceSuffix } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D5: dismiss the workspace's unread notices.
export async function POST(request: NextRequest) {
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;
  return proxyMutation(request, "POST", `/proactive/notices/dismiss-all${suffix}`, { withBody: false });
}
