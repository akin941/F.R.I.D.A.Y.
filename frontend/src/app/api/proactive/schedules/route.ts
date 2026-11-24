import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D3: the workspace's proactive schedules (owner, web session).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/proactive/schedules", { required: ["workspace"], uuids: ["workspace"] });
}
