import type { NextRequest } from "next/server";
import { proxyGet, proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 13b D6 (ADR-018): the direct media class's owner settings -- the
// kill switch and the volume maximum (owner, web session, personal
// workspace only; enforced by the backend).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/connectors/home-assistant/direct", {
    required: ["workspace"],
    uuids: ["workspace"],
  });
}

export async function PATCH(request: NextRequest) {
  return proxyMutation(request, "PATCH", "/connectors/home-assistant/direct");
}
