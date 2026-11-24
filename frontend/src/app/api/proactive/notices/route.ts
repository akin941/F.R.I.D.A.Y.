import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D5: the workspace's notices (`state=unread|all`).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/proactive/notices", {
    required: ["workspace"],
    optional: ["state"],
    uuids: ["workspace"],
  });
}
