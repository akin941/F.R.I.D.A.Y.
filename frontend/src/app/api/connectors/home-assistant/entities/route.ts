import type { NextRequest } from "next/server";
import { proxyGet, proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 13 D11: the owner's Home Assistant entity allowlist (owner, web
// session only; enforced by the backend).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/connectors/home-assistant/entities", {
    required: ["workspace"],
    uuids: ["workspace"],
  });
}

export async function POST(request: NextRequest) {
  return proxyMutation(request, "POST", "/connectors/home-assistant/entities");
}
