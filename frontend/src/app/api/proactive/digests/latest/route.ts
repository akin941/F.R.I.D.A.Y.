import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D6: the workspace's newest morning digest (or `digest: null`).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/proactive/digests/latest", { required: ["workspace"], uuids: ["workspace"] });
}
