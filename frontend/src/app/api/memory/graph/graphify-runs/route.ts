import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 8B branch 9: the Graphify run list of one workspace (status, shas,
// counts; owner, web; backend `GET /harness/graphify-runs`).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/harness/graphify-runs", { required: ["workspace"], uuids: ["workspace"] }, []);
}
