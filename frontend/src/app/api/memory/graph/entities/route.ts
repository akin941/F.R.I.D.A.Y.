import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 8B branch 9: entity search of one workspace (owner, web; the backend
// scopes with `for_actor()` and audits the read).
export async function GET(request: NextRequest) {
  return proxyGet(
    request,
    "/memory/graph/entities",
    { required: ["workspace"], optional: ["q", "kind"], uuids: ["workspace"] },
    [],
  );
}
