import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 13 D11: the allowlist editor's pick list -- one live, audited
// `ha_states` read on the backend. Reads only.
export async function GET(request: NextRequest) {
  return proxyGet(request, "/connectors/home-assistant/candidates", {
    required: ["workspace"],
    uuids: ["workspace"],
  });
}
