import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D8: the wake routes and the workspace's aliases (owner, web session).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/assistant/wake-routes", { required: ["workspace"], uuids: ["workspace"] });
}
