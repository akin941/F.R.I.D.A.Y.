import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Core memory blocks of one workspace (Phase 8A D7): owner, web session.
export async function GET(request: NextRequest) {
  return proxyGet(request, "/memory/core-blocks", { required: ["workspace"], uuids: ["workspace"] }, []);
}
